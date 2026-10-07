# Clinic Queue Helper

A small clinic LAN utility for displaying today's HIS registrations and managing Room 1 and Room 2 waiting queues. The application keeps its own queue data in SQLite and reads Visual FoxPro HIS data files as read-only sources.

## Windows setup

1. Install Python 3 and make `python` or the Python launcher `py` available on `PATH`.
2. From the repository folder, install the application and test dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Copy the example configuration:

   ```powershell
   Copy-Item config.example.json config.json
   ```

4. Edit `config.json` for the clinic:

   - Set `his_data_path` to the HIS data share containing `RG011M1.DBF` and, when available, `PD001M1.DBF`.
   - Add the known physician codes to `doctor_room_map`, mapping each code to room `1` or `2`. Unknown codes remain manageable in Unassigned.
   - Set `patient_name_field` to the patient-name field in `PD001M1.DBF`; leave it empty to show chart numbers until it is known.
   - Set `bind_host` and `port` for the clinic host. `0.0.0.0` makes the service reachable on its LAN interfaces; keep the host inside the clinic LAN.
   - Keep `sqlite_path` and `log_path` on an application-owned local drive, outside the HIS data directory.

5. Start the service:

   ```powershell
   .\run.ps1
   ```

   The launcher reports how to create `config.json` if it is missing. If the configured port is already in use, stop the other application or change `port` in the configuration.

## Open the queue

On the server, open `http://127.0.0.1:8000`. From another clinic computer, open `http://<server-lan-ip>:8000`, replacing the address with the LAN IPv4 address of the Windows host. If another computer cannot connect, check that Windows Firewall allows the configured port on the clinic's private network.

Room views use `/?room=1` and `/?room=2`; the default `/` view shows both rooms.
The completed list stays collapsed until opened, and Unassigned patients can be sent to either room from the page. Room queues support presence, overdue, call/current/next, and reorder controls. Browsers check for shared queue changes every second; the server checks HIS data every 500 ms by default and runs a full recovery scan every 45 seconds.

If the HIS share is briefly unavailable, the last saved queue remains visible with a sync warning while the server retries. New registrations receive a configurable 300-second NEW highlight. These actions only change the local SQLite queue state; they never write back to HIS files.

## Application data and HIS safety

The SQLite database and local log are stored at the configured application paths. HIS DBF, CDX, and FPT files are read-only and are never used to store application data. Tests use synthetic fixtures; do not point a test at writable HIS data.
