GATE Event Hub

Event Management and Registration System

GATE Event Hub is a web-based event management system built with Flask and Python. It allows administrators to manage events, monitor participant registrations, and track attendance, while participants can create accounts, browse available events, and reserve their spots.

Features

Admin Panel

Dashboard with event and registration statistics

Create, view, edit, and delete events

Manage participant accounts

View event registrations

Monitor and update attendance status

Search and manage event information

Participant Portal

Participant account registration and login

Browse upcoming events

Search and filter events by category

View event details and available slots

Reserve spots for events

View and cancel eligible reservations

View profile and attendance information

Security and Access Control

Separate administrator and participant access

Session-based authentication

Password handling through the application's authentication system

Server-side API routes for application operations

Sensitive configuration managed through environment variables

Technology Stack

Backend: Python, Flask

Frontend: HTML, CSS, JavaScript

Data Storage: JSON

Deployment: Render

WSGI Server: Gunicorn

Project Structure

GATE-Event-Hub/
├── app.py
├── data.json
├── requirements.txt
├── templates/
│   ├── index.html
│   └── admin.html
└── static/
    ├── styles.css
    └── script.js
