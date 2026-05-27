from django.contrib.auth.models import AbstractUser

from django.db import models

class CustomUser(AbstractUser):
    nombre = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    cargo = models.CharField(max_length=255)
    username = models.CharField(max_length=150, unique=True)    
    id_usuario_antares= models.IntegerField(blank=True, null=True)
    usuario_antares= models.IntegerField(blank=True, null=True)

    groups = models.ManyToManyField(
        'auth.Group',
        related_name='custom_user_groups',
        blank=True,
        help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='custom_user_permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        verbose_name='user permissions',
    )


