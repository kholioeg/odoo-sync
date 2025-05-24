from django.db import models

class OdooContact(models.Model):
    odoo_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=50, null=True, blank=True)
    street = models.CharField(max_length=255, null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    zip_code = models.CharField(max_length=20, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)
    last_synced = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
