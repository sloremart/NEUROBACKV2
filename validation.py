from django.core.exceptions import ImproperlyConfigured
from django.db.utils import Error

class DatabaseValidation:
    def __init__(self, connection):
        self.connection = connection

    def check(self, **kwargs):
        issues = []
        issues.extend(self._check_sql_mode(**kwargs))
        return issues

    def _check_sql_mode(self, **kwargs):
        sql_mode = self.connection.sql_mode
        modes = set(sql_mode.split(','))
        modes.remove('STRICT_TRANS_TABLES')
        sql_mode = ','.join(modes)

        if 'STRICT_TRANS_TABLES' in modes:
            return [Error(
                '%s database backend does not support the STRICT_TRANS_TABLES mode.' % (
                    self.connection.display_name()
                ),
                id='mysql.E001',
            )]
        return []
