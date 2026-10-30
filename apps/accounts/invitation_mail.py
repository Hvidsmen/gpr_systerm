"""Encrypted per-company SMTP configuration with an environment fallback."""
import base64,hashlib
from email.utils import formataddr
from urllib.parse import urlsplit,urlunsplit
from django import forms
from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from .invitation_models import CompanyMailSettings


def secret_key(company_id):
    from cryptography.fernet import Fernet
    seed=('portal.smtp.v1\0'+str(company_id)+'\0'+settings.SECRET_KEY).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed).digest()))


def encrypt_password(company_id,password):
    return secret_key(company_id).encrypt(password.encode()).decode()


def decrypt_password(company_id,value):
    if not value:return ''
    from cryptography.fernet import InvalidToken
    try:return secret_key(company_id).decrypt(value.encode()).decode()
    except (InvalidToken,ValueError,UnicodeError):raise ValidationError('Не удалось прочитать пароль почты. Введите его заново в настройках отправки.')


def portal_address(value):
    forms.URLField().clean(value)
    url=urlsplit(value)
    if url.scheme not in {'http','https'} or url.username or url.password or url.query or url.fragment:raise ValidationError('Укажите адрес портала http/https без логина, пароля и параметров.')
    return urlunsplit((url.scheme,url.netloc,url.path.rstrip('/')+'/', '', ''))


def configuration(company):
    row=CompanyMailSettings.objects.filter(company=company).first()
    if row and row.host:
        return dict(host=row.host,port=row.port,username=row.username,password=decrypt_password(company.pk,row.password_encrypted),from_email=row.from_email,security=row.security,portal_url=portal_address(row.portal_url))
    return dict(host=settings.EMAIL_HOST,port=settings.EMAIL_PORT,username=settings.EMAIL_HOST_USER,password=settings.EMAIL_HOST_PASSWORD,from_email=settings.DEFAULT_FROM_EMAIL,security='SSL' if settings.EMAIL_USE_SSL else 'STARTTLS' if settings.EMAIL_USE_TLS else 'NONE',portal_url=portal_address(settings.PORTAL_PUBLIC_URL))


def ready(company):
    try:
        cfg=configuration(company)
        if not cfg['host']:raise ValidationError('Настройте SMTP-сервер в разделе «Исходящая почта». IMAP используется только для чтения писем.')
        forms.EmailField().clean(cfg['from_email'])
        if cfg['username'] and not cfg['password']:raise ValidationError('В настройках исходящей почты не указан пароль почтового ящика.')
        return cfg,''
    except ValidationError as exc:return None,' '.join(exc.messages)


def connection(cfg):
    return mail.get_connection(host=cfg['host'],port=cfg['port'],username=cfg['username'],password=cfg['password'],use_tls=cfg['security']=='STARTTLS',use_ssl=cfg['security']=='SSL',timeout=settings.EMAIL_TIMEOUT,fail_silently=False)


def sender(cfg):return formataddr(('ЯДС ОС',cfg['from_email']))


class MailSettingsForm(forms.ModelForm):
    password=forms.CharField(label='Пароль почтового ящика',required=False,strip=False,widget=forms.PasswordInput(render_value=False,attrs={'class':'form-control','autocomplete':'new-password'}),help_text='Оставьте пустым, чтобы сохранить прежний пароль. Пароль хранится зашифрованным.')
    clear_password=forms.BooleanField(label='Очистить сохранённый пароль',required=False)
    class Meta:
        model=CompanyMailSettings
        fields=['host','port','security','username','from_email','portal_url']
    def __init__(self,*args,company,**kwargs):
        self.company=company;super().__init__(*args,**kwargs);self.instance.company=company
        for name,field in self.fields.items():
            if name!='clear_password':field.widget.attrs['class']='form-select' if isinstance(field.widget,forms.Select) else 'form-control'
        self.fields['host'].required=True;self.fields['from_email'].required=True
        self.fields['host'].help_text='Нужен сервер исходящей почты SMTP, а не IMAP. Сервер и порт уточните у почтового провайдера.'
    def clean_host(self):
        value=self.cleaned_data['host'].strip()
        if any(c.isspace() or c in '/@\\' for c in value):raise ValidationError('Укажите имя SMTP-сервера без протокола, пути и пробелов.')
        return value
    def clean_port(self):
        value=self.cleaned_data['port']
        if not 1<=value<=65535:raise ValidationError('Порт должен быть от 1 до 65535.')
        return value
    def clean_portal_url(self):return portal_address(self.cleaned_data['portal_url'])
    def clean(self):
        values=super().clean()
        if values.get('username') and not values.get('password') and (values.get('clear_password') or not self.instance.password_encrypted):
            self.add_error('password','Укажите пароль почтового ящика для SMTP.')
        return values
    def save(self,commit=True):
        row=super().save(commit=False)
        if self.cleaned_data.get('clear_password'):row.password_encrypted=''
        if self.cleaned_data.get('password'):row.password_encrypted=encrypt_password(self.company.pk,self.cleaned_data['password'])
        if commit:row.save()
        return row
