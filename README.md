# SEGLC Schedule Analytics

This workspace contains a Django backend and a React + Vite frontend for schedule analytics.

## Backend

Install dependencies and run the Django server:

```powershell
cd c:\Users\aogunsola\seglc_backend
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

The backend exposes:
- `POST /api/upload/` to upload `.xlsx`, `.csv`, or `.xer` schedule files
- `POST /api/metrics/` to compute portfolio metrics from activity JSON

## Frontend

Install dependencies and start the dev server:

```powershell
cd c:\Users\aogunsola\seglc_backend\frontend
npm install
npm run dev
```

The frontend is configured to proxy `/api` requests to `http://localhost:8000`.

## Notes

- The frontend entrypoint is `frontend/src/main.tsx`
- The main UI is implemented in `frontend/App.tsx`
- The backend app is located in `scheduler/`
