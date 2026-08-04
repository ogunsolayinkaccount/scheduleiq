import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'seglc_backend.settings')
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

username = 'admin'
email = 'admin@example.com'
password = 'AdminPass123!'

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, email, password)
    print('Superuser created: username=%s password=%s' % (username, password))
else:
    print('Superuser already exists: username=%s' % username)
