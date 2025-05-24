import logging
import json # For dumping dicts to JSON strings
from django.test import TestCase
from django.conf import settings
from unittest.mock import patch, MagicMock
import requests # For creating mock HTTP responses
# Ok and Error are not directly used for mock response construction anymore, but kept for conceptual clarity
from jsonrpcclient import Ok, Error 

from odoo_sync.models import OdooContact
from odoo_sync.cron import SyncOdooContactsCronJob

# Suppress most logging output during tests to keep test output clean
logging.disable(logging.CRITICAL)

class TestSyncOdooContactsCronJobRefactored(TestCase):

    def setUp(self):
        self.cron_job = SyncOdooContactsCronJob()
        # Store and set mock Odoo settings
        self.original_odoo_settings = {
            'ODOO_URL': getattr(settings, 'ODOO_URL', None),
            'ODOO_DB': getattr(settings, 'ODOO_DB', None),
            'ODOO_USERNAME': getattr(settings, 'ODOO_USERNAME', None),
            'ODOO_PASSWORD': getattr(settings, 'ODOO_PASSWORD', None),
        }
        settings.ODOO_URL = 'http://fake-odoo-url.com'
        settings.ODOO_DB = 'fake_db'
        settings.ODOO_USERNAME = 'fake_user'
        settings.ODOO_PASSWORD = 'fake_password'

    def tearDown(self):
        # Restore original settings
        settings.ODOO_URL = self.original_odoo_settings['ODOO_URL']
        settings.ODOO_DB = self.original_odoo_settings['ODOO_DB']
        settings.ODOO_USERNAME = self.original_odoo_settings['ODOO_USERNAME']
        settings.ODOO_PASSWORD = self.original_odoo_settings['ODOO_PASSWORD']
        OdooContact.objects.all().delete() # Clean up database

    def _prepare_mock_response(self, status_code, is_jsonrpc_ok=True, result_data=None, error_message=None, error_code=-32000, error_data_detail=None, text_data_override=None):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = status_code
        mock_resp.url = f"{settings.ODOO_URL}/jsonrpc" # Ensure response has a URL

        if text_data_override is not None:
            mock_resp.text = text_data_override
        elif is_jsonrpc_ok:
            response_dict = {"jsonrpc": "2.0", "id": 1, "result": result_data}
            mock_resp.text = json.dumps(response_dict)
        else: # JSON-RPC Error
            response_dict = {
                "jsonrpc": "2.0", 
                "id": 1, 
                "error": {"code": error_code, "message": error_message, "data": error_data_detail}
            }
            mock_resp.text = json.dumps(response_dict)

        # Set reason based on status code for HTTPError __str__
        if status_code == 500: mock_resp.reason = "Server Error"
        elif status_code == 503: mock_resp.reason = "Service Unavailable"
        elif status_code == 400: mock_resp.reason = "Bad Request"
        elif status_code == 401: mock_resp.reason = "Unauthorized"
        elif status_code == 403: mock_resp.reason = "Forbidden"
        elif status_code == 404: mock_resp.reason = "Not Found"
        elif status_code < 400 : mock_resp.reason = "OK" 
        else: mock_resp.reason = "Error" 

        mock_request = MagicMock(spec=requests.Request)
        mock_resp.request = mock_request 

        if status_code >= 400:
            http_error_instance = requests.exceptions.HTTPError(response=mock_resp)
            mock_resp.raise_for_status.side_effect = http_error_instance
        else:
            mock_resp.raise_for_status.return_value = None
            
        return mock_resp

    @patch('odoo_sync.cron.requests.post')
    def test_successful_sync_new_contact(self, mock_post):
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=123)
        contact_list = [{
            'id': 1, 'name': 'Test Contact 1', 'email': 'test1@example.com',
            'phone': '1234567890', 'street': '123 Test St', 'city': 'Test City',
            'zip': '12345', 'country_id': [10, 'Testland']
        }]
        search_read_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=contact_list)
        
        mock_post.side_effect = [auth_response, search_read_response]
        
        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 1)
        contact = OdooContact.objects.first()
        self.assertEqual(contact.odoo_id, 1)
        self.assertEqual(contact.name, 'Test Contact 1')
        self.assertEqual(contact.country, 'Testland')

    @patch('odoo_sync.cron.requests.post')
    def test_successful_sync_update_existing_contact(self, mock_post):
        OdooContact.objects.create(odoo_id=2, name='Original Name', email='original@example.com')
        
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=123)
        updated_contact_data = [{
            'id': 2, 'name': 'Updated Name', 'email': 'updated@example.com', 'phone': '9876543210'
        }]
        search_read_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=updated_contact_data)
        
        mock_post.side_effect = [auth_response, search_read_response]

        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 1)
        contact = OdooContact.objects.get(odoo_id=2)
        self.assertEqual(contact.name, 'Updated Name')
        self.assertEqual(contact.email, 'updated@example.com')
        self.assertEqual(contact.phone, '9876543210')

    @patch('odoo_sync.cron.requests.post')
    @patch('odoo_sync.cron.logger')
    def test_odoo_authentication_failure_jsonrpc_error(self, mock_logger, mock_post):
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=False, error_message="Invalid credentials", error_data_detail={"debug": "traceback..."})
        mock_post.return_value = auth_response

        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 0)
        mock_logger.error.assert_any_call("Odoo authentication failed. Error: Invalid credentials Data: {'debug': 'traceback...'}")

    @patch('odoo_sync.cron.requests.post')
    @patch('odoo_sync.cron.logger')
    def test_odoo_authentication_failure_http_error(self, mock_logger, mock_post):
        response_text = "Internal Server Error text from Odoo"
        auth_response = self._prepare_mock_response(500, text_data_override=response_text) 
        mock_post.return_value = auth_response

        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 0)
        # Updated assertion based on new logging format in cron.py
        expected_log_message = (
            f"HTTP error during Odoo request to {settings.ODOO_URL}/jsonrpc: "
            f"Status 500 Server Error. Response text snippet: {response_text}"
        )
        mock_logger.error.assert_any_call(expected_log_message)


    @patch('odoo_sync.cron.requests.post')
    @patch('odoo_sync.cron.logger')
    def test_odoo_authentication_failure_network_error(self, mock_logger, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("Failed to connect")

        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 0)
        mock_logger.error.assert_any_call(f"Request exception during Odoo request to {settings.ODOO_URL}/jsonrpc: Failed to connect")

    @patch('odoo_sync.cron.requests.post')
    @patch('odoo_sync.cron.logger')
    def test_odoo_search_read_failure_jsonrpc_error(self, mock_logger, mock_post):
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=123)
        search_read_error_response = self._prepare_mock_response(200, is_jsonrpc_ok=False, error_message="Access Denied", error_data_detail={"type": "security_error"})
        
        mock_post.side_effect = [auth_response, search_read_error_response]

        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 0)
        mock_logger.error.assert_any_call("Failed to fetch contacts from Odoo. Error: Access Denied Data: {'type': 'security_error'}")

    @patch('odoo_sync.cron.requests.post')
    @patch('odoo_sync.cron.logger')
    def test_odoo_search_read_failure_http_error(self, mock_logger, mock_post):
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=123)
        response_text = "Service Unavailable text from Odoo"
        search_read_http_error_response = self._prepare_mock_response(503, text_data_override=response_text)
        mock_post.side_effect = [auth_response, search_read_http_error_response]

        self.cron_job.do()
        self.assertEqual(OdooContact.objects.count(), 0)
        expected_log_message = (
            f"HTTP error during Odoo request to {settings.ODOO_URL}/jsonrpc: "
            f"Status 503 Service Unavailable. Response text snippet: {response_text}"
        )
        mock_logger.error.assert_any_call(expected_log_message)


    @patch('odoo_sync.cron.requests.post')
    def test_data_mapping_country_id_format_and_false(self, mock_post):
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=123)
        contacts_data = [
            {'id': 3, 'name': 'Contact with Country', 'country_id': [20, 'Specific Country']},
            {'id': 4, 'name': 'Contact No Country', 'country_id': False}, 
            {'id': 5, 'name': 'Contact Null Country', 'country_id': None} 
        ]
        search_read_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=contacts_data)
        
        mock_post.side_effect = [auth_response, search_read_response]
        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 3)
        contact_3 = OdooContact.objects.get(odoo_id=3)
        self.assertEqual(contact_3.country, 'Specific Country')
        
        contact_4 = OdooContact.objects.get(odoo_id=4)
        self.assertIsNone(contact_4.country)

        contact_5 = OdooContact.objects.get(odoo_id=5)
        self.assertIsNone(contact_5.country)

    @patch('odoo_sync.cron.requests.post')
    @patch('odoo_sync.cron.logger')
    def test_empty_search_read_result(self, mock_logger, mock_post):
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=123)
        search_read_empty_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=[])
        
        mock_post.side_effect = [auth_response, search_read_empty_response]
        
        OdooContact.objects.create(odoo_id=99, name="PreExisting")

        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 1) 
        self.assertTrue(OdooContact.objects.filter(odoo_id=99).exists())
        mock_logger.info.assert_any_call("Successfully fetched 0 contacts from Odoo.")
        mock_logger.info.assert_any_call("Synchronization complete. 0 new contacts created, 0 contacts updated.")

    @patch('odoo_sync.cron.logger')
    def test_settings_not_configured(self, mock_logger):
        original_url = settings.ODOO_URL
        settings.ODOO_URL = None 
        
        self.cron_job.do()
        
        mock_logger.error.assert_called_with("Odoo connection parameters are not fully configured in settings.")
        self.assertEqual(OdooContact.objects.count(), 0)
        
        settings.ODOO_URL = original_url
    
    @patch('odoo_sync.cron.requests.post')
    def test_successful_sync_contact_clears_fields_with_none(self, mock_post):
        OdooContact.objects.create(
            odoo_id=7, name='Contact Seven', email='seven@example.com', phone='7777777'
        )
        
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=123)
        updated_contact_data = [{
            'id': 7, 'name': 'Contact Seven Updated', 'email': None, 'phone': '777-NEW'
        }]
        search_read_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=updated_contact_data)
        
        mock_post.side_effect = [auth_response, search_read_response]

        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 1)
        contact = OdooContact.objects.get(odoo_id=7)
        self.assertEqual(contact.name, 'Contact Seven Updated')
        self.assertIsNone(contact.email, "Email should have been cleared to None")
        self.assertEqual(contact.phone, '777-NEW')

    @patch('odoo_sync.cron.requests.post')
    @patch('odoo_sync.cron.logger')
    def test_odoo_login_returns_false_uid(self, mock_logger, mock_post):
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=False)
        mock_post.return_value = auth_response

        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 0)
        mock_logger.error.assert_any_call("Odoo authentication failed. Odoo responded with 'False' for UID. Full response result: False")


    @patch('odoo_sync.cron.requests.post')
    @patch('odoo_sync.cron.logger')
    def test_malformed_json_response_from_odoo(self, mock_logger, mock_post):
        auth_response = self._prepare_mock_response(200, is_jsonrpc_ok=True, result_data=123)
        response_text = "This is not JSON {"
        malformed_response = self._prepare_mock_response(200, text_data_override=response_text)
        
        mock_post.side_effect = [auth_response, malformed_response]

        self.cron_job.do()

        self.assertEqual(OdooContact.objects.count(), 0)
        
        # Check that *one* of the error calls contains the expected message
        # The first error will be about processing, the second (if http error also leads to parse attempt) about parsing the error response.
        # Here, the HTTP call is 200, so only the "Error processing" log should appear from _make_odoo_request's main try-except.
        found_log = False
        for call_args in mock_logger.error.call_args_list:
            logged_message = str(call_args[0][0])
            if "Error processing Odoo request or response" in logged_message and \
               ("Expecting value: line 1 column 1 (char 0)" in logged_message): # Specific to json.JSONDecodeError
                found_log = True
                break
        self.assertTrue(found_log, f"Expected log message for malformed JSON not found. Logs: {mock_logger.error.call_args_list}")
