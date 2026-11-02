"""Invitation SMTP encrypts even with an untrusted, mismatched server certificate."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import smtplib
import ssl
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
from django.core.mail.backends.locmem import EmailBackend as MemoryBackend
from django.test import SimpleTestCase, override_settings

from .invitation_mail import connection


@override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                   EMAIL_SSL_CERTFILE=None, EMAIL_SSL_KEYFILE=None)
class InvitationCertificatePolicyTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        directory=TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'wrong-host.invalid')])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
              .public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('wrong-host.invalid')]),False)
              .sign(key,hashes.SHA256()))
        root=Path(directory.name)
        (root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        (root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        cls.server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_context.load_cert_chain(root/'cert.pem',root/'key.pem')

    def encrypted_handshake(self,context):
        """Real OpenSSL handshake over memory BIOs, without network access."""
        client_in,client_out,server_in,server_out=(ssl.MemoryBIO() for _ in range(4))
        client=context.wrap_bio(client_in,client_out,server_hostname='smtp.test.invalid')
        server=self.server_context.wrap_bio(server_in,server_out,server_side=True)
        client_done=server_done=False
        for _ in range(30):
            if not client_done:
                try:client.do_handshake();client_done=True
                except (ssl.SSLWantReadError,ssl.SSLWantWriteError):pass
            outgoing=client_out.read()
            if outgoing:server_in.write(outgoing)
            if not server_done:
                try:server.do_handshake();server_done=True
                except (ssl.SSLWantReadError,ssl.SSLWantWriteError):pass
            outgoing=server_out.read()
            if outgoing:client_in.write(outgoing)
            if client_done and server_done:
                self.assertIsNotNone(client.cipher())
                self.assertGreaterEqual(context.minimum_version,ssl.TLSVersion.TLSv1_2)
                return
        self.fail('TLS handshake did not finish')

    def config(self,security='STARTTLS'):
        return dict(host='smtp.test.invalid',port=587 if security=='STARTTLS' else 465,
                    security=security,username='synthetic-user',password='synthetic-password')

    def test_fixture_certificate_is_rejected_by_normal_verification(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.encrypted_handshake(ssl.create_default_context())

    @patch('django.core.mail.backends.smtp.DNS_NAME.get_fqdn',return_value='portal.test.invalid')
    def test_starttls_and_implicit_tls_encrypt_probe_and_send_without_cert_verification(self,_fqdn):
        for security,transport in [('STARTTLS','SMTP'),('SSL','SMTP_SSL')]:
            with self.subTest(security=security):
                wire=MagicMock(spec=['starttls','login','sendmail','quit','close'])
                wire.sendmail.return_value={}
                encrypted=False

                def handshake(context):
                    nonlocal encrypted
                    self.encrypted_handshake(context)
                    encrypted=True

                def connect(*args,**kwargs):
                    if security=='SSL':handshake(kwargs['context'])
                    return wire

                def login(*args):
                    self.assertTrue(encrypted,'Credentials must follow a successful TLS handshake')

                wire.starttls.side_effect=handshake
                wire.login.side_effect=login
                with patch.object(smtplib,transport,side_effect=connect):
                    backend=connection(self.config(security))
                    self.assertEqual(backend.ssl_context.verify_mode,ssl.CERT_NONE)
                    self.assertFalse(backend.ssl_context.check_hostname)
                    self.assertTrue(backend.open())
                    wire.login.assert_called_once_with('synthetic-user','synthetic-password')
                    wire.sendmail.assert_not_called()  # Settings probe only connects and logs in.
                    message=EmailMessage('Synthetic invitation','Synthetic body',
                        'sender@example.invalid',['recipient@example.invalid'])
                    self.assertEqual(backend.send_messages([message]),1)
                    wire.sendmail.assert_called_once()
                    backend.close()
                if security=='STARTTLS':
                    wire.starttls.assert_called_once_with(context=backend.ssl_context)
                else:wire.starttls.assert_not_called()

    @patch('django.core.mail.backends.smtp.DNS_NAME.get_fqdn',return_value='portal.test.invalid')
    @patch('smtplib.SMTP')
    def test_tls_failure_never_falls_back_to_plaintext_login_or_send(self,smtp,_fqdn):
        smtp.return_value.starttls.side_effect=ssl.SSLError('synthetic TLS failure')
        backend=connection(self.config())
        with self.assertRaises(ssl.SSLError):backend.open()
        smtp.return_value.login.assert_not_called()
        smtp.return_value.sendmail.assert_not_called()
        self.assertIsNone(backend.connection)

    def test_global_tls_and_unrelated_smtp_backends_keep_certificate_verification(self):
        invitation=connection(self.config())
        normal=SMTPBackend(host='smtp.test.invalid',port=587,use_tls=True,use_ssl=False)
        for context in [ssl.create_default_context(),normal.ssl_context]:
            self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
        self.assertIsNot(invitation.ssl_context,normal.ssl_context)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_non_smtp_backend_is_preserved(self):
        backend=connection(self.config())
        self.assertIsInstance(backend,MemoryBackend)
        self.assertNotIn('ssl_context',backend.__dict__)
