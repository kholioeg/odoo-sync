from django.contrib import admin
from .models import OdooContact

@admin.register(OdooContact)
class OdooContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'last_synced')
    search_fields = ('name', 'email')
