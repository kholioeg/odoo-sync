# Odoo Contact Sync with Django

## Description
This project implements a Django application to synchronize contact information from an Odoo instance to a local Django database. It uses a cron job to periodically fetch contacts (Partners) from Odoo via its JSON-RPC API and stores/updates them in the Django `OdooContact` model.

## Setup Instructions

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd <repository_directory_name>
    ```

2.  **Create a virtual environment and install dependencies:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    pip install -r requirements.txt
    ```

3.  **Configure Odoo Connection Details:**
    Open `project/settings.py` and update the following placeholder values with your Odoo instance details:
    ```python
    ODOO_URL = 'YOUR_ODOO_URL'         # e.g., 'http://localhost:8069'
    ODOO_DB = 'YOUR_ODOO_DATABASE'     # e.g., 'mydatabase'
    ODOO_USERNAME = 'YOUR_ODOO_USERNAME' # e.g., 'admin'
    ODOO_PASSWORD = 'YOUR_ODOO_PASSWORD' # e.g., 'odoo_password'
    ```

4.  **Run Django Migrations:**
    Apply the database migrations to create the necessary tables:
    ```bash
    python manage.py migrate
    ```

## Running the Synchronization

1.  **Manual Synchronization:**
    To run the contact synchronization job manually, execute the following command:
    ```bash
    python manage.py runcrons
    ```
    This will trigger the `SyncOdooContactsCronJob` defined in the application. Check the console output for logs regarding the synchronization process.

2.  **Periodic Execution (Scheduling):**
    For automatic periodic synchronization (e.g., daily, as configured in `odoo_sync/cron.py`), you need to set up a system cron job or a scheduler (like systemd timers on Linux, Task Scheduler on Windows, or a cloud provider's scheduler) to execute the `python manage.py runcrons` command.

    Example crontab entry for daily execution at 2 AM:
    ```cron
    0 2 * * * /path/to/your/project/venv/bin/python /path/to/your/project/manage.py runcrons >> /path/to/your/project/cron.log 2>&1
    ```
    Ensure you use the correct paths to your project's virtual environment and `manage.py` script.

## Running the Development Server (Optional)
To run the Django development server (e.g., to access the Django admin interface):
```bash
python manage.py createsuperuser  # If you haven't created an admin user yet
python manage.py runserver
```
You can then access the admin panel at `http://127.0.0.1:8000/admin/` to view the synced contacts.
