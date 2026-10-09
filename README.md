# Clinic Queue Helper

A small clinic LAN utility for displaying today's HIS registrations and managing the 一診 and 二診 waiting queues. The application keeps shared queue data in its server-side SQLite database and reads Visual FoxPro HIS data files as read-only sources.

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
   - Physician filters are entered on the queue page under 一診 and 二診. The fields are shared by every workstation connected to this server and save when focus leaves the field. Leave a field blank when that room has no session. Each non-empty `CCDOC` code can belong to only one room.
   - For an existing installation, `doctor_room_map` in `config.json` is used only to seed an empty room filter the first time the database starts. New installations can leave it empty and enter the codes on the page.
   - The current HIS schema uses `NUM` for the registration's patient number and in `PD001M1.DBF`, with `NAME` for the patient name. Set `patient_number_field` to the matching patient-number field in `PD001M1.DBF` and `patient_name_field` to the name field; leave the name field empty to show chart numbers instead.
   - `rg011m1_visit_filename` selects the optional VISIT source and defaults to `RG011M1_VISIT.DBF`. `visit_monitor_enabled` defaults to `false`; enable it only for the experimental, read-only monitor.
   - Set `bind_host` and `port` for the clinic host. `0.0.0.0` makes the service reachable on its LAN interfaces; keep the host inside the clinic LAN.
   - Keep `sqlite_path` and `log_path` on an application-owned local drive, outside the HIS data directory.

5. Start the service:

   ```powershell
   .\run.ps1
   ```

   The launcher reports how to create `config.json` if it is missing. If the configured port is already in use, stop the other application or change `port` in the configuration.

## Open the queue

On the server, open `http://127.0.0.1:8000`. From another clinic computer, open `http://<server-lan-ip>:8000`, replacing the address with the LAN IPv4 address of the Windows host. If another computer cannot connect, check that Windows Firewall allows the configured port on the clinic's private network.

Room views use `/?room=1` for 一診 and `/?room=2` for 二診; the default `/` view shows both rooms. Room membership comes only from matching the HIS `CCDOC` value to the two shared filters. Encounters matching neither filter remain visible in a separate read-only section; staff cannot manually assign them. A blank room filter means that room has no active session and no queue.

The completed list stays collapsed until opened. `診別未確認` is also collapsed by default and displays a record count in its heading. During testing, it remains visible even when the count is zero. Room queues support presence, overdue, and reorder controls; HIS handles patient calling.

The optional VISIT monitor is an experimental, read-only observation surface. It reports candidates, unmatched candidates, diagnostics, and source health only; it does not change queue membership, order, presence, HIS data, or the official visit state, and it never emits `ACTIVE`. See the controlled procedure in [docs/visit-manual-validation.md](docs/visit-manual-validation.md) before enabling it against a clinic-approved test source.

Browsers check for shared queue changes every second. A page reload waits while the session selector or a physician-code field is focused, and while a physician-code save is in progress. Expanded per-patient registration details remain expanded across reloads in the same browser tab. Changing a room filter saves that field when it loses focus; the latest saved value is shared across workstations. The server checks HIS data every 500 ms by default and runs a full recovery scan every 45 seconds.

If the HIS share is briefly unavailable, the last saved queue remains visible with a sync warning while the server retries. New registrations receive a configurable 300-second NEW highlight. These actions only change the local SQLite queue state; they never write back to HIS files.

## Application data and HIS safety

The SQLite database and local log are stored at the configured application paths. HIS DBF, CDX, and FPT files are read-only and are never used to store application data. Tests use synthetic fixtures; do not point a test at writable HIS data.
