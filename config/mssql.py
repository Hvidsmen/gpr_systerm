"""Small dependency-free helpers used before the portal switches to MSSQL."""


def odbc_value(value):
    return '{'+str(value).replace('}','}}')+'}'


def integrated_connection_string(server,database,driver='ODBC Driver 18 for SQL Server',trust_certificate=True):
    parts=[f'DRIVER={odbc_value(driver)}',f'SERVER={odbc_value(server)}',
           f'DATABASE={odbc_value(database)}','Trusted_Connection=yes','Encrypt=yes']
    if trust_certificate:parts.append('TrustServerCertificate=yes')
    return ';'.join(parts)+';'
