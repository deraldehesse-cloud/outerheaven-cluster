# Google Services (Calendar & Drive) Knowledge Base

## Service Account Authentifizierung
- **Credentials-Pfad**: /home/fox/.hermes/google_service_account.json (bzw. auf Outer Heaven hinterlegt)
- **Scopes**:
  - Google Calendar: https://www.googleapis.com/auth/calendar.readonly
  - Google Drive: https://www.googleapis.com/auth/drive.readonly

## Kalender-Integration
- Kalender-ID: Primär oder dedizierte Arbeits-Kalender-ID.
- Event-Query: timeMin = datetime.datetime.utcnow().isoformat() + 'Z', singleEvents=True, orderBy='startTime'.

## Drive-Integration
- Mime-Types:
  - Ordner: application/vnd.google-apps.folder
  - PDFs / Dokumente: application/pdf, text/markdown
- File Queries: 'trashed = false' and name contains '...'
