FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY meal_planner.py .
COPY shopping_list.py .
COPY household_calendar.py .
COPY calendar_feed.py .
COPY household_sharing.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
