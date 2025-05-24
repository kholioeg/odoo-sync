import logging
import requests # For making HTTP requests
from django_cron import CronJobBase, Schedule
from django.conf import settings
from odoo_sync.models import OdooContact
# Use request_json to build the payload, and parse_json to parse the response.
# Ok and Error are used to check the parsed response.
from jsonrpcclient import Error, Ok, parse_json 
from jsonrpcclient.requests import request_json # Correct way to get request_json

logger = logging.getLogger(__name__)

class SyncOdooContactsCronJob(CronJobBase):
    RUN_EVERY_MINS = 1440  # Run once a day
    code = 'odoo_sync.sync_odoo_contacts_cron_job'
    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)

    def _make_odoo_request(self, url, method, service=None, **params):
        """
        Helper function to make a JSON-RPC request to Odoo.
        """
        # For 'common' service (login), the structure is slightly different
        if service == 'common' and method == 'login':
            # Odoo's login method expects db, login, password as direct params in the call
            payload = request_json(method, params=params)
        else:
            # For 'object' service (execute_kw), structure includes service, method, and args/kwargs
             payload = request_json(
                "call",  # This is the JSON-RPC method for execute_kw style calls
                params={
                    "service": service,
                    "method": method,
                    "args": [
                        params.get('db'),
                        params.get('uid'),
                        params.get('password'),
                        params.get('model'),
                        params.get('operation'), # e.g., 'search_read'
                        params.get('domain', []),
                        params.get('kwargs', {})
                    ],
                },
            )
        
        try:
            response = requests.post(url, json=payload, timeout=20) # Increased timeout
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            # It's important to use response.text with parse_json, not response.json()
            return parse_json(response.text) 
        except requests.exceptions.Timeout:
            logger.error(f"Timeout during Odoo request to {url} for method {method}")
            return None # Or an Error object
        except requests.exceptions.HTTPError as e:
            # Log a more controlled message using attributes from e.response
            error_details = f"Status {e.response.status_code} {e.response.reason}."
            # Include a snippet of response text if it's not too long or binary
            try:
                response_text_snippet = e.response.text[:200] # Limit snippet length
            except Exception:
                response_text_snippet = "[Could not get response text]"
            logger.error(f"HTTP error during Odoo request to {url}: {error_details} Response text snippet: {response_text_snippet}")
            
            # Attempt to parse error response from Odoo if available (useful if Odoo returns JSON error for HTTP error status)
            if e.response is not None and e.response.text:
                try:
                    return parse_json(e.response.text)
                except Exception as parse_e: # JSONDecodeError or other parsing issue
                    logger.error(f"Could not parse error response from Odoo: {parse_e}")
            return None # Or an Error object
        except requests.exceptions.RequestException as e: # Catch other request exceptions
            logger.error(f"Request exception during Odoo request to {url}: {e}")
            return None # Or an Error object
        except Exception as e: # Catch other errors like JSON parsing issues from valid HTTP responses
             logger.error(f"Error processing Odoo request or response ({url}, {method}): {e}")
             return None


    def do(self):
        logger.info("Starting Odoo contacts synchronization cron job.")

        ODOO_URL = getattr(settings, 'ODOO_URL', None)
        ODOO_DB = getattr(settings, 'ODOO_DB', None)
        ODOO_USERNAME = getattr(settings, 'ODOO_USERNAME', None)
        ODOO_PASSWORD = getattr(settings, 'ODOO_PASSWORD', None)

        if not all([ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD]):
            logger.error("Odoo connection parameters are not fully configured in settings.")
            return

        # Odoo Authentication
        auth_url = f"{ODOO_URL}/jsonrpc"
        uid = None
        
        auth_params = {
            "db": ODOO_DB,
            "login": ODOO_USERNAME,
            "password": ODOO_PASSWORD
        }
        # The 'login' method is part of the 'common' service (or sometimes 'db' service in older Odoo versions)
        # jsonrpcclient.request_json will build the full JSON-RPC request structure.
        # The actual RPC method Odoo expects here is 'login' (or 'authenticate').
        parsed_response = self._make_odoo_request(auth_url, method="login", service="common", **auth_params)

        if parsed_response is None: # Error handled and logged by _make_odoo_request
            return
        if isinstance(parsed_response, Ok):
            uid = parsed_response.result
            if not uid and isinstance(uid, bool): # Specifically if uid is False (boolean)
                logger.error(f"Odoo authentication failed. Odoo responded with 'False' for UID. Full response result: {parsed_response.result}")
                return
            elif not uid: # Other falsy UIDs (e.g. None, 0, empty string), less common for login success
                logger.error(f"Odoo authentication failed. UID received: {uid}. Full response result: {parsed_response.result}")
                return
            logger.info(f"Successfully authenticated with Odoo. UID: {uid}")
        else: # Error object
            logger.error(f"Odoo authentication failed. Error: {parsed_response.message} Data: {getattr(parsed_response, 'data', None)}")
            return
        
        if not uid: # Should be redundant if above logic is correct
            logger.error("Odoo authentication failed, UID not obtained.")
            return

        # Data Fetching
        models_url = f"{ODOO_URL}/jsonrpc" # Same URL for object calls
        fields_to_fetch = ['id', 'name', 'email', 'phone', 'street', 'city', 'zip', 'country_id']
        
        fetch_params = {
            'db': ODOO_DB,
            'uid': uid,
            'password': ODOO_PASSWORD, # Odoo requires password for execute_kw calls
            'model': 'res.partner',
            'operation': 'search_read', # This is the Odoo model method
            'domain': [], # Domain to fetch all partners
            'kwargs': {'fields': fields_to_fetch}
        }
        # 'execute_kw' is the method on Odoo's 'object' service
        contacts_response = self._make_odoo_request(models_url, method="execute_kw", service="object", **fetch_params)

        contacts_data = []
        if contacts_response is None: # Error handled by _make_odoo_request
            return
        if isinstance(contacts_response, Ok):
            contacts_data = contacts_response.result
            logger.info(f"Successfully fetched {len(contacts_data)} contacts from Odoo.")
        else: # Error object
            logger.error(f"Failed to fetch contacts from Odoo. Error: {contacts_response.message} Data: {getattr(contacts_response, 'data', None)}")
            return

        # Data Syncing
        synced_count = 0
        updated_count = 0
        for contact_data in contacts_data:
            country_name = None
            if contact_data.get('country_id') and isinstance(contact_data['country_id'], list) and len(contact_data['country_id']) > 1:
                country_name = contact_data['country_id'][1]
            elif contact_data.get('country_id') is False: # Handle Odoo returning False for empty Many2one
                 country_name = None


            defaults = {
                'name': contact_data.get('name'),
                'email': contact_data.get('email'),
                'phone': contact_data.get('phone'),
                'street': contact_data.get('street'),
                'city': contact_data.get('city'),
                'zip_code': contact_data.get('zip'), # Ensure this matches Odoo field 'zip'
                'country': country_name,
            }
            
            defaults = {k: v for k, v in defaults.items() if v is not None or k in ['email', 'phone', 'street', 'city', 'zip_code', 'country']} # Allow explicit None for clearing fields

            try:
                obj, created = OdooContact.objects.update_or_create(
                    odoo_id=contact_data['id'],
                    defaults=defaults
                )
                if created:
                    synced_count += 1
                else:
                    updated_count +=1
            except Exception as e:
                logger.error(f"Error syncing contact with Odoo ID {contact_data.get('id')}: {e}")
        
        logger.info(f"Synchronization complete. {synced_count} new contacts created, {updated_count} contacts updated.")
