# Everyday Intelligence — mobile web demo

A free, experimental, non-commercial local intelligence app using OpenStreetMap and Open-Meteo. Six views: parking and errands, shopping, neighborhood, outdoor, pharmacies and services, and local discovery. Results use straight-line distances rather than routing; parking occupancy, store prices, pharmacy duty and event schedules are not available. Mapped place data may be incomplete. Public upstream servers impose fair-use limits.

Deployment: on Render as a Python web service. Build: `pip install -r requirements.txt`. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`. Keep this demo low traffic and do not add private tokens to the repository.
