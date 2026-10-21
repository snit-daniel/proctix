#!/usr/bin/env python3
from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from datetime import datetime, timedelta
import uuid
import json
import uvicorn
import asyncio
import aiosqlite
import matplotlib.pyplot as plt
import io
import base64
from typing import Dict, List, Optional
import os
from contextlib import asynccontextmanager

# Database setup
DATABASE_NAME = "proctoring.db"

# Lifespan event handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    await migrate_database()
    yield
    # Shutdown
    # Any cleanup code here

app = FastAPI(title="Exam Proctoring Admin Panel", lifespan=lifespan)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database models
class ExamConfig(BaseModel):
    name: str
    duration: int
    description: str = ""
    enable_face_detection: bool = True
    enable_audio_monitoring: bool = True
    enable_behavior_monitoring: bool = True
    enable_screen_capture: bool = True
    enable_mobile_monitoring: bool = False
    max_verification_attempts: int = 3
    max_violations: int = 5

class ExamUpdate(BaseModel):
    name: Optional[str] = None
    duration: Optional[int] = None
    description: Optional[str] = None
    enable_face_detection: Optional[bool] = None
    enable_audio_monitoring: Optional[bool] = None
    enable_behavior_monitoring: Optional[bool] = None
    enable_screen_capture: Optional[bool] = None
    enable_mobile_monitoring: Optional[bool] = None
    max_verification_attempts: Optional[int] = None
    max_violations: Optional[int] = None

# Initialize database
async def init_db():
    """Initialize database with all required tables"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Create exams table - FIXED SCHEMA
        await db.execute('''
            CREATE TABLE IF NOT EXISTS exams (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                duration INTEGER NOT NULL,
                description TEXT,
                enable_face_detection INTEGER DEFAULT 1,
                enable_audio_monitoring INTEGER DEFAULT 1,
                enable_behavior_monitoring INTEGER DEFAULT 1,
                enable_screen_capture INTEGER DEFAULT 1,
                enable_mobile_monitoring INTEGER DEFAULT 0,
                max_verification_attempts INTEGER DEFAULT 3,
                max_violations INTEGER DEFAULT 5,
                created_at TEXT,
                updated_at TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')

        # Create exam_sessions table to link exams with sessions
        await db.execute('''
            CREATE TABLE IF NOT EXISTS exam_sessions (
                exam_id TEXT,
                session_id TEXT,
                assigned_at TEXT,
                PRIMARY KEY (exam_id, session_id),
                FOREIGN KEY (exam_id) REFERENCES exams (id),
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        ''')

        # Create sessions table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT,
                student_id TEXT,
                student_name TEXT,
                exam_name TEXT,
                exam_duration INTEGER,
                verification_status TEXT,
                mobile_connected INTEGER,
                mobile_status TEXT,
                face_detection_status TEXT,
                audio_monitoring_status TEXT,
                behavior_monitoring_status TEXT,
                verification_attempts INTEGER,
                started_at TEXT,
                ended_at TEXT,
                submission_status TEXT,
                termination_reason TEXT,
                total_violations INTEGER,
                severity_level TEXT,
                review_status TEXT,
                exam_id TEXT  -- Added exam_id to sessions table
            )
        ''')

        # Create violations table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS violations (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                type TEXT,
                timestamp TEXT,
                details TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        ''')

        # Create screenshots table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS screenshots (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                timestamp TEXT,
                image_data BLOB,
                format TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        ''')

        # Create recordings table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS recordings (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                start_time TEXT,
                end_time TEXT,
                fps INTEGER,
                quality INTEGER,
                frame_count INTEGER,
                video_path TEXT,
                video_filename TEXT,
                file_size INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        ''')

        # Create id_images table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS id_images (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                image_data BLOB,
                upload_time TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        ''')

        # Create face_images table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS face_images (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                image_data BLOB,
                capture_time TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        ''')

        # Create students table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS students (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                created_at TEXT
            )
        ''')

        # Insert sample students if they don't exist
        sample_students = [
            ("123456", "John Smith", "john.smith@example.com", datetime.now().isoformat()),
            ("654321", "Jane Doe", "jane.doe@example.com", datetime.now().isoformat()),
            ("2024001", "Alice Johnson", "alice.johnson@example.com", datetime.now().isoformat()),
            ("2024002", "Bob Williams", "bob.williams@example.com", datetime.now().isoformat()),
        ]

        for student in sample_students:
            await db.execute(
                "INSERT OR IGNORE INTO students (id, name, email, created_at) VALUES (?, ?, ?, ?)",
                student
            )

        # Insert sample exams if they don't exist - FIXED: Now has correct number of values (14)
        sample_exams = [
            ("mid-term-001", "Mid Term Exam", 120, "Mid term examination", 1, 1, 1, 1, 0, 3, 5, 
             datetime.now().isoformat(), datetime.now().isoformat(), 1),
            ("final-001", "Final Exam", 180, "Final examination", 1, 1, 1, 1, 1, 3, 5, 
             datetime.now().isoformat(), datetime.now().isoformat(), 1),
        ]

        for exam in sample_exams:
            await db.execute(
                '''INSERT OR IGNORE INTO exams 
                (id, name, duration, description, enable_face_detection, 
                enable_audio_monitoring, enable_behavior_monitoring, 
                enable_screen_capture, enable_mobile_monitoring, 
                max_verification_attempts, max_violations, created_at, updated_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                exam
            )

        await db.commit()

# Add this function to your admin code
async def migrate_database():
    """Migrate database to latest schema"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Check if exams table has the new columns
        cursor = await db.execute("PRAGMA table_info(exams)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        # Add missing columns
        if 'description' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN description TEXT")
        if 'enable_face_detection' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN enable_face_detection INTEGER DEFAULT 1")
        if 'enable_audio_monitoring' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN enable_audio_monitoring INTEGER DEFAULT 1")
        if 'enable_behavior_monitoring' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN enable_behavior_monitoring INTEGER DEFAULT 1")
        if 'enable_screen_capture' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN enable_screen_capture INTEGER DEFAULT 1")
        if 'enable_mobile_monitoring' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN enable_mobile_monitoring INTEGER DEFAULT 0")
        if 'max_verification_attempts' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN max_verification_attempts INTEGER DEFAULT 3")
        if 'max_violations' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN max_violations INTEGER DEFAULT 5")
        if 'created_at' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN created_at TEXT")
        if 'updated_at' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN updated_at TEXT")
        if 'is_active' not in column_names:
            await db.execute("ALTER TABLE exams ADD COLUMN is_active INTEGER DEFAULT 1")
        
        # Check if sessions table has exam_id column
        cursor = await db.execute("PRAGMA table_info(sessions)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        if 'exam_id' not in column_names:
            await db.execute("ALTER TABLE sessions ADD COLUMN exam_id TEXT")
        
        # Create exam_sessions table if it doesn't exist
        await db.execute('''
            CREATE TABLE IF NOT EXISTS exam_sessions (
                exam_id TEXT,
                session_id TEXT,
                assigned_at TEXT,
                PRIMARY KEY (exam_id, session_id),
                FOREIGN KEY (exam_id) REFERENCES exams (id),
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        ''')
        
        await db.commit()


# Routes
@app.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Exam Proctoring Admin</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            :root {
                --primary: #4361ee;
                --secondary: #3f37c9;
                --success: #4cc9f0;
                --danger: #f72585;
                --warning: #f8961e;
                --info: #4895ef;
                --light: #f8f9fa;
                --dark: #212529;
            }
            .exam-id {
                font-size: 0.8rem;
                color: #666;
                margin-top: 4px;
            }
            
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #f5f7fb;
                color: #333;
            }
            
            .container {
                display: flex;
                min-height: 100vh;
            }
            
            .sidebar {
                width: 250px;
                background: var(--primary);
                color: white;
                padding: 20px 0;
                box-shadow: 2px 0 10px rgba(0,0,0,0.1);
            }
            
            .sidebar-header {
                padding: 0 20px 20px;
                border-bottom: 1px solid rgba(255,255,255,0.1);
                margin-bottom: 20px;
            }
            
            .sidebar-header h2 {
                font-size: 1.5rem;
            }
            
            .nav-item {
                padding: 12px 20px;
                cursor: pointer;
                transition: background 0.3s;
            }
            
            .nav-item:hover, .nav-item.active {
                background: rgba(255,255,255,0.1);
            }
            
            .main-content {
                flex: 1;
                padding: 20px;
                overflow-y: auto;
            }
            
            .header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 30px;
                padding-bottom: 15px;
                border-bottom: 1px solid #e0e0e0;
            }
            
            .card {
                background: white;
                border-radius: 10px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                padding: 20px;
                margin-bottom: 20px;
            }
            
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }
            
            .stat-card {
                background: white;
                border-radius: 10px;
                padding: 20px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                text-align: center;
            }
            
            .stat-number {
                font-size: 2.5rem;
                font-weight: bold;
                color: var(--primary);
                margin: 10px 0;
            }
            
            .chart-container {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
                margin-bottom: 30px;
            }
            
            .chart-card {
                background: white;
                border-radius: 10px;
                padding: 20px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }
            
            .chart {
                height: 300px;
                position: relative;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 20px;
            }
            
            th, td {
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid #e0e0e0;
            }
            
            th {
                background-color: #f8f9fa;
                font-weight: 600;
            }
            
            .btn {
                padding: 8px 16px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-weight: 500;
                transition: all 0.3s;
            }
            
            .btn-primary {
                background: var(--primary);
                color: white;
            }
            
            .btn-danger {
                background: var(--danger);
                color: white;
            }
            
            .btn-success {
                background: var(--success);
                color: white;
            }
            
            .modal {
                display: none;
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0,0,0,0.5);
                z-index: 1000;
            }
            
            .modal-content {
                background: white;
                width: 500px;
                max-width: 90%;
                margin: 100px auto;
                padding: 20px;
                border-radius: 10px;
            }
            
            .form-group {
                margin-bottom: 15px;
            }
            
            label {
                display: block;
                margin-bottom: 5px;
                font-weight: 500;
            }
            
            input, select, textarea {
                width: 100%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-size: 1rem;
            }
            
            .checkbox-group {
                display: flex;
                align-items: center;
                gap: 10px;
            }
            
            .checkbox-group input {
                width: auto;
            }
            
            .tab-content {
                display: none;
            }
            
            .tab-content.active {
                display: block;
            }
            
            .status-badge {
                padding: 4px 8px;
                border-radius: 20px;
                font-size: 0.8rem;
                font-weight: 500;
            }
            
            .status-completed {
                background: #e7f6e7;
                color: #0b8c0b;
            }
            
            .status-in-progress {
                background: #e8f4fd;
                color: #0b6bcb;
            }
            
            .status-terminated {
                background: #fde8e8;
                color: #cb0b0b;
            }
            
            .severity-low {
                color: #28a745;
            }
            
            .severity-medium {
                color: #ffc107;
            }
            
            .severity-high {
                color: #fd7e14;
            }
            
            .severity-critical {
                color: #dc3545;
            }
            
            .exam-id {
                font-size: 0.8rem;
                color: #666;
                margin-top: 4px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="sidebar">
                <div class="sidebar-header">
                    <h2>Exam Proctor Admin</h2>
                </div>
                <div class="nav-item active" data-tab="dashboard">Dashboard</div>
                <div class="nav-item" data-tab="exams">Exam Management</div>
                <div class="nav-item" data-tab="sessions">Session Monitoring</div>
                <div class="nav-item" data-tab="settings">Settings</div>
            </div>
            
            <div class="main-content">
                <div class="header">
                    <h1>Admin Dashboard</h1>
                    <div>
                        <span id="current-date"></span>
                    </div>
                </div>
                
                <!-- Dashboard Tab -->
                <div id="dashboard" class="tab-content active">
                    <div class="stats-grid">
                        <div class="stat-card">
                            <h3>Total Sessions</h3>
                            <div class="stat-number" id="total-sessions">0</div>
                            <p>All time</p>
                        </div>
                        <div class="stat-card">
                            <h3>Active Sessions</h3>
                            <div class="stat-number" id="active-sessions">0</div>
                            <p>Currently in progress</p>
                        </div>
                        <div class="stat-card">
                            <h3>Violations Today</h3>
                            <div class="stat-number" id="violations-today">0</div>
                            <p>Last 24 hours</p>
                        </div>
                        <div class="stat-card">
                            <h3>Success Rate</h3>
                            <div class="stat-number" id="success-rate">0%</div>
                            <p>Completed successfully</p>
                        </div>
                    </div>
                    
                    <div class="chart-container">
                        <div class="chart-card">
                            <h3>Session Status Distribution</h3>
                            <div class="chart">
                                <canvas id="status-chart"></canvas>
                            </div>
                        </div>
                        <div class="chart-card">
                            <h3>Violations by Type</h3>
                            <div class="chart">
                                <canvas id="violation-chart"></canvas>
                            </div>
                        </div>
                    </div>
                    
                    <div class="chart-card">
                        <h3>Sessions Over Time</h3>
                        <div class="chart">
                            <canvas id="time-chart"></canvas>
                        </div>
                    </div>
                    
                    <div class="card">
                        <h3>Recent Sessions</h3>
                        <table id="recent-sessions">
                            <thead>
                                <tr>
                                    <th>Student</th>
                                    <th>Exam</th>
                                    <th>Start Time</th>
                                    <th>Status</th>
                                    <th>Violations</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                <!-- Will be populated by JavaScript -->
                            </tbody>
                        </table>
                    </div>
                </div>
                
                <!-- Exams Tab -->
                <div id="exams" class="tab-content">
                    <div class="header">
                        <h2>Exam Management</h2>
                        <button class="btn btn-primary" id="create-exam-btn">Create New Exam</button>
                    </div>
                    
                    <div class="card">
                        <h3>Active Exams</h3>
                        <table id="exams-table">
                            <thead>
                                <tr>
                                    <th>Exam Name</th>
                                    <th>Exam ID</th>
                                    <th>Duration</th>
                                    <th>Face Detection</th>
                                    <th>Audio Monitoring</th>
                                    <th>Screen Capture</th>
                                    <th>Created</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                <!-- Will be populated by JavaScript -->
                            </tbody>
                        </table>
                    </div>
                </div>
                
                <!-- Sessions Tab -->
                <div id="sessions" class="tab-content">
                    <div class="header">
                        <h2>Session Monitoring</h2>
                        <div>
                            <select id="exam-filter">
                                <option value="">All Exams</option>
                            </select>
                        </div>
                    </div>
                    
                    <div class="card">
                        <h3>All Sessions</h3>
                        <table id="sessions-table">
                            <thead>
                                <tr>
                                    <th>Student</th>
                                    <th>Exam</th>
                                    <th>Start Time</th>
                                    <th>End Time</th>
                                    <th>Status</th>
                                    <th>Violations</th>
                                    <th>Severity</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                <!-- Will be populated by JavaScript -->
                            </tbody>
                        </table>
                    </div>
                </div>
                
                <!-- Settings Tab -->
                <div id="settings" class="tab-content">
                    <div class="header">
                        <h2>System Settings</h2>
                    </div>
                    
                    <div class="card">
                        <h3>General Settings</h3>
                        <div class="form-group">
                            <label for="system-name">System Name</label>
                            <input type="text" id="system-name" value="Exam Proctoring System">
                        </div>
                        <div class="form-group">
                            <label for="max-violations">Maximum Violations Before Termination</label>
                            <input type="number" id="max-violations" value="5" min="1" max="20">
                        </div>
                        <div class="form-group">
                            <label for="screenshot-interval">Screenshot Interval (seconds)</label>
                            <input type="number" id="screenshot-interval" value="5" min="1" max="60">
                        </div>
                        <div class="form-group">
                            <label for="recording-fps">Recording FPS</label>
                            <input type="number" id="recording-fps" value="10" min="1" max="30">
                        </div>
                        <button class="btn btn-primary" id="save-settings">Save Settings</button>
                    </div>
                    
                    <div class="card">
                        <h3>Feature Toggles</h3>
                        <div class="form-group checkbox-group">
                            <input type="checkbox" id="face-detection" checked>
                            <label for="face-detection">Enable Face Detection</label>
                        </div>
                        <div class="form-group checkbox-group">
                            <input type="checkbox" id="audio-monitoring" checked>
                            <label for="audio-monitoring">Enable Audio Monitoring</label>
                        </div>
                        <div class="form-group checkbox-group">
                            <input type="checkbox" id="behavior-monitoring" checked>
                            <label for="behavior-monitoring">Enable Behavior Monitoring</label>
                        </div>
                        <div class="form-group checkbox-group">
                            <input type="checkbox" id="screen-capture" checked>
                            <label for="screen-capture">Enable Screen Capture</label>
                        </div>
                        <div class="form-group checkbox-group">
                            <input type="checkbox" id="mobile-monitoring">
                            <label for="mobile-monitoring">Enable Mobile Monitoring</label>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Create Exam Modal -->
        <div id="create-exam-modal" class="modal">
            <div class="modal-content">
                <h2>Create New Exam</h2>
                <div class="form-group">
                    <label for="exam-name">Exam Name</label>
                    <input type="text" id="exam-name" required>
                </div>
                <div class="form-group">
                    <label for="exam-duration">Duration (minutes)</label>
                    <input type="number" id="exam-duration" value="60" min="1" max="360">
                </div>
                <div class="form-group">
                    <label for="exam-description">Description</label>
                    <textarea id="exam-description" rows="3"></textarea>
                </div>
                
                <h3>Proctoring Settings</h3>
                <div class="form-group checkbox-group">
                    <input type="checkbox" id="exam-face-detection" checked>
                    <label for="exam-face-detection">Enable Face Detection</label>
                </div>
                <div class="form-group checkbox-group">
                    <input type="checkbox" id="exam-audio-monitoring" checked>
                    <label for="exam-audio-monitoring">Enable Audio Monitoring</label>
                </div>
                <div class="form-group checkbox-group">
                    <input type="checkbox" id="exam-behavior-monitoring" checked>
                    <label for="exam-behavior-monitoring">Enable Behavior Monitoring</label>
                </div>
                <div class="form-group checkbox-group">
                    <input type="checkbox" id="exam-screen-capture" checked>
                    <label for="exam-screen-capture">Enable Screen Capture</label>
                </div>
                <div class="form-group checkbox-group">
                    <input type="checkbox" id="exam-mobile-monitoring">
                    <label for="exam-mobile-monitoring">Enable Mobile Monitoring</label>
                </div>
                
                <div class="form-group">
                    <label for="max-verification-attempts">Max Verification Attempts</label>
                    <input type="number" id="max-verification-attempts" value="3" min="1" max="10">
                </div>
                <div class="form-group">
                    <label for="max-violations-exam">Max Violations Before Termination</label>
                    <input type="number" id="max-violations-exam" value="5" min="1" max="20">
                </div>
                
                <div style="display: flex; justify-content: space-between; margin-top: 20px;">
                    <button class="btn btn-danger" id="close-exam-modal">Cancel</button>
                    <button class="btn btn-primary" id="save-exam">Create Exam</button>
                </div>
            </div>
        </div>
        
        <!-- Session Details Modal -->
        <div id="session-details-modal" class="modal">
            <div class="modal-content" style="width: 800px; max-height: 80vh; overflow-y: auto;">
                <h2>Session Details</h2>
                <div id="session-details-content">
                    <!-- Will be populated by JavaScript -->
                </div>
                <div style="text-align: center; margin-top: 20px;">
                    <button class="btn btn-primary" id="close-session-modal">Close</button>
                </div>
            </div>
        </div>

        <script>
            // Global variables
            let currentTab = 'dashboard';
            let exams = [];
            let sessions = [];
            let statistics = {};
            
            // DOM elements
            const tabContents = document.querySelectorAll('.tab-content');
            const navItems = document.querySelectorAll('.nav-item');
            const currentDateElement = document.getElementById('current-date');
            
            // Initialize the dashboard
            document.addEventListener('DOMContentLoaded', function() {
                // Set current date
                currentDateElement.textContent = new Date().toLocaleDateString('en-US', {
                    weekday: 'long',
                    year: 'numeric',
                    month: 'long',
                    day: 'numeric'
                });
                
                // Load initial data
                loadDashboardData();
                loadExams();
                loadSessions();
                
                // Set up tab navigation
                navItems.forEach(item => {
                    item.addEventListener('click', function() {
                        const tab = this.getAttribute('data-tab');
                        switchTab(tab);
                    });
                });
                
                // Set up modal handlers
                document.getElementById('create-exam-btn').addEventListener('click', function() {
                    document.getElementById('create-exam-modal').style.display = 'block';
                });
                
                document.getElementById('close-exam-modal').addEventListener('click', function() {
                    document.getElementById('create-exam-modal').style.display = 'none';
                });
                
                document.getElementById('save-exam').addEventListener('click', createExam);
                
                document.getElementById('close-session-modal').addEventListener('click', function() {
                    document.getElementById('session-details-modal').style.display = 'none';
                });
                
                document.getElementById('save-settings').addEventListener('click', saveSettings);
                
                // Close modals when clicking outside
                window.addEventListener('click', function(event) {
                    if (event.target.classList.contains('modal')) {
                        event.target.style.display = 'none';
                    }
                });
                
                // Set up exam filter
                document.getElementById('exam-filter').addEventListener('change', loadSessions);
            });
            
            // Switch between tabs
            function switchTab(tabName) {
                // Update UI
                tabContents.forEach(tab => tab.classList.remove('active'));
                navItems.forEach(item => item.classList.remove('active'));
                
                document.getElementById(tabName).classList.add('active');
                document.querySelector(`.nav-item[data-tab="${tabName}"]`).classList.add('active');
                
                currentTab = tabName;
                
                // Load data for the selected tab if needed
                if (tabName === 'dashboard') {
                    loadDashboardData();
                } else if (tabName === 'exams') {
                    loadExams();
                } else if (tabName === 'sessions') {
                    loadSessions();
                }
            }
            
            // Load dashboard data
            async function loadDashboardData() {
                try {
                    // Load statistics
                    const statsResponse = await fetch('/api/statistics?days=30');
                    statistics = await statsResponse.json();
                    
                    // Update stats cards
                    document.getElementById('total-sessions').textContent = 
                        statistics.status_counts.reduce((sum, item) => sum + item.count, 0);
                    
                    // This would ideally come from a different endpoint in a real system
                    document.getElementById('active-sessions').textContent = 
                        statistics.status_counts.find(item => item.status === 'in_progress')?.count || 0;
                    
                    document.getElementById('violations-today').textContent = 
                        statistics.violation_types.reduce((sum, item) => sum + item.count, 0);
                    
                    const completed = statistics.status_counts.find(item => item.status === 'submitted')?.count || 0;
                    const total = statistics.status_counts.reduce((sum, item) => sum + item.count, 0);
                    const successRate = total > 0 ? Math.round((completed / total) * 100) : 0;
                    document.getElementById('success-rate').textContent = `${successRate}%`;
                    
                    // Create charts
                    createStatusChart(statistics.status_counts);
                    createViolationChart(statistics.violation_types);
                    createTimeChart(statistics.sessions_over_time);
                    
                    // Load recent sessions
                    const sessionsResponse = await fetch('/api/sessions?limit=10');
                    const sessionsData = await sessionsResponse.json();
                    sessions = sessionsData.sessions;
                    
                    populateRecentSessions(sessions);
                    
                } catch (error) {
                    console.error('Error loading dashboard data:', error);
                }
            }
            
            // Create status distribution chart
            function createStatusChart(statusData) {
                const ctx = document.getElementById('status-chart').getContext('2d');
                
                if (window.statusChart) {
                    window.statusChart.destroy();
                }
                
                const labels = statusData.map(item => {
                    // Convert status to more readable format
                    const statusMap = {
                        'not_started': 'Not Started',
                        'in_progress': 'In Progress',
                        'submitted': 'Submitted',
                        'terminated': 'Terminated'
                    };
                    return statusMap[item.status] || item.status;
                });
                
                const backgroundColors = [
                    '#4361ee', '#4cc9f0', '#3a0ca3', '#f72585', '#7209b7'
                ];
                
                window.statusChart = new Chart(ctx, {
                    type: 'doughnut',
                    data: {
                        labels: labels,
                        datasets: [{
                            data: statusData.map(item => item.count),
                            backgroundColor: backgroundColors,
                            borderWidth: 1
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                position: 'bottom'
                            }
                        }
                    }
                });
            }
            
            // Create violation type chart
            function createViolationChart(violationData) {
                const ctx = document.getElementById('violation-chart').getContext('2d');
                
                if (window.violationChart) {
                    window.violationChart.destroy();
                }
                
                const labels = violationData.map(item => {
                    // Convert violation types to more readable format
                    const typeMap = {
                        'no_face': 'No Face Detected',
                        'multiple_faces': 'Multiple Faces',
                        'face_obscured': 'Face Obscured',
                        'mobile_phone': 'Mobile Phone Detected',
                        'second_person': 'Second Person Detected',
                        'multiple_monitors': 'Multiple Monitors',
                        'tab_switch': 'Tab Switch',
                        'audio_detected': 'Audio Detected'
                    };
                    return typeMap[item.type] || item.type;
                });
                
                window.violationChart = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Number of Violations',
                            data: violationData.map(item => item.count),
                            backgroundColor: '#4361ee',
                            borderWidth: 1
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {
                            y: {
                                beginAtZero: true
                            }
                        },
                        plugins: {
                            legend: {
                                display: false
                            }
                        }
                    }
                });
            }
            
            // Create sessions over time chart
            function createTimeChart(timeData) {
                const ctx = document.getElementById('time-chart').getContext('2d');
                
                if (window.timeChart) {
                    window.timeChart.destroy();
                }
                
                window.timeChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: timeData.map(item => {
                            // Format date as MM/DD
                            const date = new Date(item.date);
                            return `${date.getMonth() + 1}/${date.getDate()}`;
                        }),
                        datasets: [{
                            label: 'Sessions',
                            data: timeData.map(item => item.count),
                            backgroundColor: 'rgba(67, 97, 238, 0.2)',
                            borderColor: 'rgba(67, 97, 238, 1)',
                            borderWidth: 2,
                            tension: 0.3,
                            fill: true
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {
                            y: {
                                beginAtZero: true
                            }
                        }
                    }
                });
            }
            
            // Populate recent sessions table
            function populateRecentSessions(sessions) {
                const tbody = document.querySelector('#recent-sessions tbody');
                tbody.innerHTML = '';
                
                sessions.forEach(session => {
                    const tr = document.createElement('tr');
                    
                    // Format status badge
                    let statusClass = '';
                    let statusText = '';
                    
                    switch(session.submission_status) {
                        case 'submitted':
                            statusClass = 'status-completed';
                            statusText = 'Completed';
                            break;
                        case 'in_progress':
                            statusClass = 'status-in-progress';
                            statusText = 'In Progress';
                            break;
                        case 'terminated':
                            statusClass = 'status-terminated';
                            statusText = 'Terminated';
                            break;
                        default:
                            statusClass = '';
                            statusText = session.submission_status;
                    }
                    
                    // Format start time
                    const startTime = session.started_at ? 
                        new Date(session.started_at).toLocaleString() : 'Not started';
                    
                    tr.innerHTML = `
                        <td>${session.student_name} (${session.student_id})</td>
                        <td>${session.exam_name || 'N/A'}</td>
                        <td>${startTime}</td>
                        <td><span class="status-badge ${statusClass}">${statusText}</span></td>
                        <td>${session.total_violations}</td>
                        <td>
                            <button class="btn btn-primary view-session" data-id="${session.id}">View</button>
                        </td>
                    `;
                    
                    tbody.appendChild(tr);
                });
                
                // Add event listeners to view buttons
                document.querySelectorAll('.view-session').forEach(button => {
                    button.addEventListener('click', function() {
                        const sessionId = this.getAttribute('data-id');
                        viewSessionDetails(sessionId);
                    });
                });
            }
            
            // Load exams
            async function loadExams() {
                try {
                    const response = await fetch('/api/exams');
                    const data = await response.json();
                    exams = data.exams;
                    
                    populateExamsTable(exams);
                    
                    // Also update exam filter dropdown in sessions tab
                    const examFilter = document.getElementById('exam-filter');
                    examFilter.innerHTML = '<option value="">All Exams</option>';
                    
                    exams.forEach(exam => {
                        const option = document.createElement('option');
                        option.value = exam.id;
                        option.textContent = exam.name;
                        examFilter.appendChild(option);
                    });
                    
                } catch (error) {
                    console.error('Error loading exams:', error);
                }
            }
            
            // Populate exams table
            function populateExamsTable(exams) {
                const tbody = document.querySelector('#exams-table tbody');
                tbody.innerHTML = '';
                
                exams.forEach(exam => {
                    const tr = document.createElement('tr');
                    
                    // Format created date
                    const createdDate = exam.created_at ? 
                        new Date(exam.created_at).toLocaleDateString() : 'Unknown';
                    
                    tr.innerHTML = `
                        <td>
                            ${exam.name}
                            <div class="exam-id">ID: ${exam.id}</div>
                        </td>
                        <td>${exam.id}</td>
                        <td>${exam.duration} minutes</td>
                        <td>${exam.enable_face_detection ? 'Yes' : 'No'}</td>
                        <td>${exam.enable_audio_monitoring ? 'Yes' : 'No'}</td>
                        <td>${exam.enable_screen_capture ? 'Yes' : 'No'}</td>
                        <td>${createdDate}</td>
                        <td>
                            <button class="btn btn-primary edit-exam" data-id="${exam.id}">Edit</button>
                            <button class="btn btn-danger delete-exam" data-id="${exam.id}">Delete</button>
                        </td>
                    `;
                    
                    tbody.appendChild(tr);
                });
                
                // Add event listeners to action buttons
                document.querySelectorAll('.edit-exam').forEach(button => {
                    button.addEventListener('click', function() {
                        const examId = this.getAttribute('data-id');
                        editExam(examId);
                    });
                });
                
                document.querySelectorAll('.delete-exam').forEach(button => {
                    button.addEventListener('click', function() {
                        const examId = this.getAttribute('data-id');
                        deleteExam(examId);
                    });
                });
            }
            
            // Create a new exam
            async function createExam() {
                try {
                    const examData = {
                        name: document.getElementById('exam-name').value,
                        duration: parseInt(document.getElementById('exam-duration').value),
                        description: document.getElementById('exam-description').value,
                        enable_face_detection: document.getElementById('exam-face-detection').checked,
                        enable_audio_monitoring: document.getElementById('exam-audio-monitoring').checked,
                        enable_behavior_monitoring: document.getElementById('exam-behavior-monitoring').checked,
                        enable_screen_capture: document.getElementById('exam-screen-capture').checked,
                        enable_mobile_monitoring: document.getElementById('exam-mobile-monitoring').checked,
                        max_verification_attempts: parseInt(document.getElementById('max-verification-attempts').value),
                        max_violations: parseInt(document.getElementById('max-violations-exam').value)
                    };
                    
                    const response = await fetch('/api/exams', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(examData)
                    });
                    
                    if (response.ok) {
                        // Close modal and refresh exams list
                        document.getElementById('create-exam-modal').style.display = 'none';
                        loadExams();
                        
                        // Reset form
                        document.getElementById('exam-name').value = '';
                        document.getElementById('exam-duration').value = '60';
                        document.getElementById('exam-description').value = '';
                    } else {
                        alert('Error creating exam');
                    }
                    
                } catch (error) {
                    console.error('Error creating exam:', error);
                    alert('Error creating exam');
                }
            }
            
            // Edit an exam (placeholder - would open a modal similar to create)
            function editExam(examId) {
                const exam = exams.find(e => e.id === examId);
                if (exam) {
                    // In a real implementation, this would populate and show an edit modal
                    alert(`Edit functionality for "${exam.name}" would be implemented here`);
                }
            }
            

            // Delete an exam
            async function deleteExam(examId) {
                if (confirm('Are you sure you want to delete this exam?')) {
                    try {
                        const response = await fetch(`/api/exams/${examId}`, {
                            method: 'DELETE'
                        });
                        
                        if (response.ok) {
                            loadExams();
                        } else {
                            alert('Error deleting exam');
                        }
                    } catch (error) {
                        console.error('Error deleting exam:', error);
                        alert('Error deleting exam');
                    }
                }
            }

            
            // Load sessions
            async function loadSessions() {
                try {
                    const examFilter = document.getElementById('exam-filter').value;
                    let url = '/api/sessions?limit=100';
                    
                    if (examFilter) {
                        url += `&exam_id=${examFilter}`;
                    }
                    
                    const response = await fetch(url);
                    const data = await response.json();
                    sessions = data.sessions;
                    
                    populateSessionsTable(sessions);
                    
                } catch (error) {
                    console.error('Error loading sessions:', error);
                }
            }
            
            // Populate sessions table
            function populateSessionsTable(sessions) {
                const tbody = document.querySelector('#sessions-table tbody');
                tbody.innerHTML = '';
                
                sessions.forEach(session => {
                    const tr = document.createElement('tr');
                    
                    // Format status badge
                    let statusClass = '';
                    let statusText = '';
                    
                    switch(session.submission_status) {
                        case 'submitted':
                            statusClass = 'status-completed';
                            statusText = 'Completed';
                            break;
                        case 'in_progress':
                            statusClass = 'status-in-progress';
                            statusText = 'In Progress';
                            break;
                        case 'terminated':
                            statusClass = 'status-terminated';
                            statusText = 'Terminated';
                            break;
                        default:
                            statusClass = '';
                            statusText = session.submission_status;
                    }
                    
                    // Format dates
                    const startTime = session.started_at ? 
                        new Date(session.started_at).toLocaleString() : 'Not started';
                    
                    const endTime = session.ended_at ? 
                        new Date(session.ended_at).toLocaleString() : 'N/A';
                    
                    // Format severity
                    let severityClass = '';
                    switch(session.severity_level) {
                        case 'low':
                            severityClass = 'severity-low';
                            break;
                        case 'medium':
                            severityClass = 'severity-medium';
                            break;
                        case 'high':
                            severityClass = 'severity-high';
                            break;
                        case 'critical':
                            severityClass = 'severity-critical';
                            break;
                    }
                    
                    tr.innerHTML = `
                        <td>${session.student_name} (${session.student_id})</td>
                        <td>${session.exam_name || 'N/A'}</td>
                        <td>${startTime}</td>
                        <td>${endTime}</td>
                        <td><span class="status-badge ${statusClass}">${statusText}</span></td>
                        <td>${session.total_violations}</td>
                        <td class="${severityClass}">${session.severity_level}</td>
                        <td>
                            <button class="btn btn-primary view-session" data-id="${session.id}">View Details</button>
                        </td>
                    `;
                    
                    tbody.appendChild(tr);
                });
                
                // Add event listeners to view buttons
                document.querySelectorAll('.view-session').forEach(button => {
                    button.addEventListener('click', function() {
                        const sessionId = this.getAttribute('data-id');
                        viewSessionDetails(sessionId);
                    });
                });
            }
            
            // In viewSessionDetails function, update the display logic:
            async function viewSessionDetails(sessionId) {
                try {
                    const response = await fetch(`/api/sessions/${sessionId}/details`);
                    const sessionDetails = await response.json();
                    
                    // Use the final_exam_name, final_exam_duration, final_exam_id from the response
                    const examName = sessionDetails.session.final_exam_name || 
                                    sessionDetails.session.exam_name || 
                                    'Unknown Exam';
                    const examDuration = sessionDetails.session.final_exam_duration || 
                                    sessionDetails.session.exam_duration || 
                                    'N/A';
                    const examId = sessionDetails.session.final_exam_id || 
                                sessionDetails.session.exam_id || 
                                'N/A';
                    
                    const content = document.getElementById('session-details-content');
                    content.innerHTML = `
                        <h3>Session Information</h3>
                        <table>
                            <tr><th>Student ID:</th><td>${sessionDetails.session.student_id}</td></tr>
                            <tr><th>Student Name:</th><td>${sessionDetails.session.student_name}</td></tr>
                            <tr><th>Exam:</th><td>${examName}</td></tr>
                            <tr><th>Exam ID:</th><td>${examId}</td></tr>
                            <tr><th>Duration:</th><td>${examDuration} minutes</td></tr>
                            <tr><th>Start Time:</th><td>${sessionDetails.session.started_at ? new Date(sessionDetails.session.started_at).toLocaleString() : 'Not started'}</td></tr>
                            <tr><th>End Time:</th><td>${sessionDetails.session.ended_at ? new Date(sessionDetails.session.ended_at).toLocaleString() : 'N/A'}</td></tr>
                            <tr><th>Status:</th><td>${sessionDetails.session.submission_status}</td></tr>
                            <tr><th>Termination Reason:</th><td>${sessionDetails.session.termination_reason || 'N/A'}</td></tr>
                            <tr><th>Total Violations:</th><td>${sessionDetails.session.total_violations}</td></tr>
                            <tr><th>Severity Level:</th><td>${sessionDetails.session.severity_level}</td></tr>
                        </table>
                        
                        <h3 style="margin-top: 20px;">Violations (${sessionDetails.violations.length})</h3>
                        ${sessionDetails.violations.length > 0 ? `
                            <table>
                                <thead>
                                    <tr>
                                        <th>Type</th>
                                        <th>Time</th>
                                        <th>Details</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    ${sessionDetails.violations.map(violation => `
                                        <tr>
                                            <td>${violation.type}</td>
                                            <td>${new Date(violation.timestamp).toLocaleString()}</td>
                                            <td>${JSON.stringify(violation.details)}</td>
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>
                        ` : '<p>No violations recorded</p>'}
                        
                        <h3 style="margin-top: 20px;">Media</h3>
                        <p>Screenshots: ${sessionDetails.screenshot_count}</p>
                        <p>Recordings: ${sessionDetails.recordings.length}</p>
                        <p>ID Images: ${sessionDetails.id_images.length}</p>
                        <p>Face Images: ${sessionDetails.face_images.length}</p>
                    `;
                    
                    // Show the modal
                    document.getElementById('session-details-modal').style.display = 'block';
                    
                } catch (error) {
                    console.error('Error loading session details:', error);
                    alert('Error loading session details');
                }
            }
            
            // Save settings
            async function saveSettings() {
                // In a real implementation, this would send the settings to the server
                alert('Settings saved successfully!');
            }
        </script>
    </body>
    </html>
    """


@app.get("/api/exams")
async def get_all_exams(active_only: bool = True):
    """Get all exams with optional filtering"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        if active_only:
            cursor = await db.execute(
                "SELECT * FROM exams WHERE is_active = 1 ORDER BY created_at DESC"
            )
        else:
            cursor = await db.execute("SELECT * FROM exams ORDER BY created_at DESC")
        
        rows = await cursor.fetchall()
        exams = []
        for row in rows:
            exams.append({
                "id": row[0],
                "name": row[1],
                "duration": row[2],
                "description": row[3],
                "enable_face_detection": bool(row[4]),
                "enable_audio_monitoring": bool(row[5]),
                "enable_behavior_monitoring": bool(row[6]),
                "enable_screen_capture": bool(row[7]),
                "enable_mobile_monitoring": bool(row[8]),
                "max_verification_attempts": row[9],
                "max_violations": row[10],
                "created_at": row[11],
                "updated_at": row[12],
                "is_active": bool(row[13])
            })
        return {"exams": exams}


@app.post("/api/exams")
async def create_exam(exam_config: ExamConfig):
    """Create a new exam configuration"""
    exam_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()
    
    async with aiosqlite.connect(DATABASE_NAME) as db:
        try:
            await db.execute(
                '''INSERT INTO exams 
                (id, name, duration, description, enable_face_detection, 
                enable_audio_monitoring, enable_behavior_monitoring, 
                enable_screen_capture, enable_mobile_monitoring, 
                max_verification_attempts, max_violations, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    exam_id,
                    exam_config.name,
                    exam_config.duration,
                    exam_config.description,
                    int(exam_config.enable_face_detection),
                    int(exam_config.enable_audio_monitoring),
                    int(exam_config.enable_behavior_monitoring),
                    int(exam_config.enable_screen_capture),
                    int(exam_config.enable_mobile_monitoring),
                    exam_config.max_verification_attempts,
                    exam_config.max_violations,
                    created_at,
                    created_at
                )
            )
            await db.commit()
            
            return {"status": "created", "exam_id": exam_id}
            
        except Exception as e:
            # If the insert fails due to missing columns, alter the table
            if "no such column" in str(e):
                await db.execute("ALTER TABLE exams ADD COLUMN description TEXT")
                await db.execute("ALTER TABLE exams ADD COLUMN enable_face_detection INTEGER DEFAULT 1")
                await db.execute("ALTER TABLE exams ADD COLUMN enable_audio_monitoring INTEGER DEFAULT 1")
                await db.execute("ALTER TABLE exams ADD COLUMN enable_behavior_monitoring INTEGER DEFAULT 1")
                await db.execute("ALTER TABLE exams ADD COLUMN enable_screen_capture INTEGER DEFAULT 1")
                await db.execute("ALTER TABLE exams ADD COLUMN enable_mobile_monitoring INTEGER DEFAULT 0")
                await db.execute("ALTER TABLE exams ADD COLUMN max_verification_attempts INTEGER DEFAULT 3")
                await db.execute("ALTER TABLE exams ADD COLUMN max_violations INTEGER DEFAULT 5")
                await db.execute("ALTER TABLE exams ADD COLUMN created_at TEXT")
                await db.execute("ALTER TABLE exams ADD COLUMN updated_at TEXT")
                await db.execute("ALTER TABLE exams ADD COLUMN is_active INTEGER DEFAULT 1")
                
                # Retry the insert
                return await create_exam(exam_config)
            else:
                raise e
            

@app.put("/api/exams/{exam_id}")
async def update_exam(exam_id: str, exam_update: ExamUpdate):
    """Update an existing exam configuration"""
    updates = {}
    if exam_update.name is not None:
        updates["name"] = exam_update.name
    if exam_update.duration is not None:
        updates["duration"] = exam_update.duration
    if exam_update.description is not None:
        updates["description"] = exam_update.description
    if exam_update.enable_face_detection is not None:
        updates["enable_face_detection"] = int(exam_update.enable_face_detection)
    if exam_update.enable_audio_monitoring is not None:
        updates["enable_audio_monitoring"] = int(exam_update.enable_audio_monitoring)
    if exam_update.enable_behavior_monitoring is not None:
        updates["enable_behavior_monitoring"] = int(exam_update.enable_behavior_monitoring)
    if exam_update.enable_screen_capture is not None:
        updates["enable_screen_capture"] = int(exam_update.enable_screen_capture)
    if exam_update.enable_mobile_monitoring is not None:
        updates["enable_mobile_monitoring"] = int(exam_update.enable_mobile_monitoring)
    if exam_update.max_verification_attempts is not None:
        updates["max_verification_attempts"] = exam_update.max_verification_attempts
    if exam_update.max_violations is not None:
        updates["max_violations"] = exam_update.max_violations
    
    if not updates:
        return {"status": "no_changes"}
    
    updates["updated_at"] = datetime.now().isoformat()
    
    set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
    values = list(updates.values())
    values.append(exam_id)
    
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            f"UPDATE exams SET {set_clause} WHERE id = ?",
            values
        )
        await db.commit()
    
    return {"status": "updated"}

@app.delete("/api/exams/{exam_id}")
async def delete_exam(exam_id: str, deactivate: bool = True):
    """Delete or deactivate an exam"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        if deactivate:
            await db.execute(
                "UPDATE exams SET is_active = 0, updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), exam_id)
            )
        else:
            await db.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
        await db.commit()
    
    return {"status": "deleted" if not deactivate else "deactivated"}

@app.get("/api/sessions")
async def get_all_sessions(exam_id: Optional[str] = None, limit: int = 100, offset: int = 0):
    """Get all sessions with optional filtering by exam"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        if exam_id:
            cursor = await db.execute('''
                SELECT s.*,
            COALESCE(e.name, s.exam_name) AS final_exam_name,
            COALESCE(e.duration, s.exam_duration) AS final_exam_duration,
            COALESCE(es.exam_id, s.exam_id) AS final_exam_id
            FROM sessions s
            LEFT JOIN exam_sessions es ON s.id = es.session_id
            LEFT JOIN exams e ON (es.exam_id = e.id OR s.exam_id = e.id)
            ORDER BY s.created_at DESC
            ''', (exam_id, limit, offset))

            

        else:
            cursor = await db.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
        
        rows = await cursor.fetchall()
        sessions = []
        for row in rows:
            sessions.append({
                "id": row[0],
                "created_at": row[1],
                "student_id": row[2],
                "student_name": row[3],
                "exam_name": row[4],
                "exam_duration": row[5],
                "verification_status": row[6],
                "mobile_connected": bool(row[7]),
                "mobile_status": row[8],
                "face_detection_status": row[9],
                "audio_monitoring_status": row[10],
                "behavior_monitoring_status": row[11],
                "verification_attempts": row[12],
                "started_at": row[13],
                "ended_at": row[14],
                "submission_status": row[15],
                "termination_reason": row[16],
                "total_violations": row[17],
                "severity_level": row[18],
                "review_status": row[19],
                "exam_id": row[20] if len(row) > 20 else None  # Handle the exam_id field
            })
        
        # Get total count
        if exam_id:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM exam_sessions WHERE exam_id = ?",
                (exam_id,)
            )
        else:
            cursor = await db.execute("SELECT COUNT(*) FROM sessions")
        total_count = (await cursor.fetchone())[0]
        
        return {
            "sessions": sessions,
            "total_count": total_count,
            "limit": limit,
            "offset": offset
        }

@app.get("/api/statistics")
async def get_statistics(exam_id: Optional[str] = None, days: int = 30):
    """Get statistics and visualizations"""
    start_date = (datetime.now() - timedelta(days=days)).isoformat()
    
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Session counts by status
        if exam_id:
            cursor = await db.execute('''
                SELECT s.submission_status, COUNT(*) 
                FROM sessions s
                JOIN exam_sessions es ON s.id = es.session_id
                WHERE es.exam_id = ? AND s.created_at >= ?
                GROUP BY s.submission_status
            ''', (exam_id, start_date))
        else:
            cursor = await db.execute('''
                SELECT submission_status, COUNT(*) 
                FROM sessions 
                WHERE created_at >= ?
                GROUP BY submission_status
            ''', (start_date,))
        
        status_counts = await cursor.fetchall()
        
        # Violations by type
        if exam_id:
            cursor = await db.execute('''
                SELECT v.type, COUNT(*) 
                FROM violations v
                JOIN sessions s ON v.session_id = s.id
                JOIN exam_sessions es ON s.id = es.session_id
                WHERE es.exam_id = ? AND v.timestamp >= ?
                GROUP BY v.type
            ''', (exam_id, start_date))
        else:
            cursor = await db.execute('''
                SELECT type, COUNT(*) 
                FROM violations 
                WHERE timestamp >= ?
                GROUP BY type
            ''', (start_date,))
        
        violation_types = await cursor.fetchall()
        
        # Sessions over time
        if exam_id:
            cursor = await db.execute('''
                SELECT DATE(s.created_at) as date, COUNT(*) 
                FROM sessions s
                JOIN exam_sessions es ON s.id = es.session_id
                WHERE es.exam_id = ? AND s.created_at >= ?
                GROUP BY DATE(s.created_at)
                ORDER BY date
            ''', (exam_id, start_date))
        else:
            cursor = await db.execute('''
                SELECT DATE(created_at) as date, COUNT(*) 
                FROM sessions 
                WHERE created_at >= ?
                GROUP BY DATE(created_at)
                ORDER by date
            ''', (start_date,))
        
        sessions_over_time = await cursor.fetchall()
    
    return {
        "status_counts": [{"status": row[0], "count": row[1]} for row in status_counts],
        "violation_types": [{"type": row[0], "count": row[1]} for row in violation_types],
        "sessions_over_time": [{"date": row[0], "count": row[1]} for row in sessions_over_time]
    }



@app.get("/api/sessions/{session_id}/details")
async def get_session_details(session_id: str):
    """Get detailed information about a specific session"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # FIXED: Get session info with proper exam data joining
        cursor = await db.execute('''
            SELECT s.*, 
                   COALESCE(e.name, s.exam_name, 'Unknown Exam') as final_exam_name,
                   COALESCE(e.duration, s.exam_duration, 60) as final_exam_duration,
                   COALESCE(es.exam_id, s.exam_id, 'pending') as final_exam_id
            FROM sessions s
            LEFT JOIN exam_sessions es ON s.id = es.session_id
            LEFT JOIN exams e ON es.exam_id = e.id OR s.exam_id = e.id
            WHERE s.id = ?
        ''', (session_id,))
        
        session_row = await cursor.fetchone()
        
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")

        # Extract exam details using proper indices
        exam_name = session_row[21]  # final_exam_name from query (index 21)
        exam_duration = session_row[22]  # final_exam_duration from query (index 22)
        exam_id = session_row[23]  # final_exam_id from query (index 23)
        
        # Get violations
        cursor = await db.execute(
            "SELECT * FROM violations WHERE session_id = ? ORDER BY timestamp",
            (session_id,)
        )
        violation_rows = await cursor.fetchall()
        
        # Format violations with proper handling for timestamp/details fields
        violations = []
        for row in violation_rows:
            # Check if the details field contains a timestamp instead of JSON
            details_str = row[4] if row[4] else "{}"
            timestamp = row[3]
            
            # If timestamp is empty but details looks like a timestamp, swap them
            if (not timestamp or timestamp == "{}") and details_str and "T" in details_str and ":" in details_str:
                # The details field contains a timestamp, so swap them
                timestamp = details_str
                details_str = "{}"
            
            # Parse details
            try:
                details = json.loads(details_str) if details_str else {}
            except json.JSONDecodeError:
                # If details is not valid JSON, use it as a raw string
                details = {"raw_data": details_str}
            
            violations.append({
                "id": row[0],
                "session_id": row[1],
                "type": row[2],
                "timestamp": timestamp,
                "details": details
            })
        
        # Get screenshots count
        cursor = await db.execute(
            "SELECT COUNT(*) FROM screenshots WHERE session_id = ?",
            (session_id,)
        )
        screenshot_count = (await cursor.fetchone())[0]
        
        # Get recordings
        cursor = await db.execute(
            "SELECT * FROM recordings WHERE session_id = ?",
            (session_id,)
        )
        recording_rows = await cursor.fetchall()
        
        # Get ID images
        cursor = await db.execute(
            "SELECT * FROM id_images WHERE session_id = ? ORDER BY upload_time",
            (session_id,)
        )
        id_image_rows = await cursor.fetchall()
        
        # Get face images
        cursor = await db.execute(
            "SELECT * FROM face_images WHERE session_id = ? ORDER BY capture_time",
            (session_id,)
        )
        face_image_rows = await cursor.fetchall()
    
    # FIXED: Correct session data mapping based on the actual table structure from your data
    session_data = {
        "id": session_row[0],                    # id
        "created_at": session_row[1],            # created_at
        "student_id": session_row[2],            # student_id
        "student_name": session_row[3],          # student_name
        "exam_id": session_row[4],               # exam_id
        "exam_name": session_row[5],             # exam_name
        "exam_duration": session_row[6],         # exam_duration
        "verification_status": session_row[7],   # verification_status
        "mobile_connected": bool(session_row[8]), # mobile_connected
        "mobile_status": session_row[9],         # mobile_status
        "face_detection_status": session_row[10], # face_detection_status
        "audio_monitoring_status": session_row[11], # audio_monitoring_status
        "behavior_monitoring_status": session_row[12], # behavior_monitoring_status
        "verification_attempts": session_row[13], # verification_attempts
        "started_at": session_row[14],           # started_at
        "ended_at": session_row[15],             # ended_at
        "submission_status": session_row[16],    # submission_status
        "termination_reason": session_row[17],   # termination_reason
        "total_violations": session_row[18],     # total_violations
        "severity_level": session_row[19],       # severity_level
        "review_status": session_row[20]         # review_status
    }
    
    # Format recordings
    recordings = []
    for row in recording_rows:
        recordings.append({
            "id": row[0],
            "session_id": row[1],
            "start_time": row[2],
            "end_time": row[3],
            "fps": row[4],
            "quality": row[5],
            "frame_count": row[6],
            "video_path": row[7],
            "video_filename": row[8],
            "file_size": row[9]
        })
    
    # Format ID images (metadata only)
    id_images = []
    for row in id_image_rows:
        id_images.append({
            "id": row[0],
            "session_id": row[1],
            "upload_time": row[3]
        })
    
    # Format face images (metadata only)
    face_images = []
    for row in face_image_rows:
        face_images.append({
            "id": row[0],
            "session_id": row[1],
            "capture_time": row[3]
        })
    
    return {
        "session": session_data,
        "violations": violations,
        "screenshot_count": screenshot_count,
        "recordings": recordings,
        "id_images": id_images,
        "face_images": face_images
    }


@app.get("/api/debug/session/{session_id}/exam-info")
async def debug_session_exam_info(session_id: str):
    """Debug endpoint to check exam information from all sources"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Check session table
        cursor = await db.execute(
            "SELECT exam_name, exam_duration, exam_id FROM sessions WHERE id = ?",
            (session_id,)
        )
        session_exam = await cursor.fetchone()
        
        # Check exam_sessions table
        cursor = await db.execute(
            "SELECT es.exam_id, e.name, e.duration FROM exam_sessions es LEFT JOIN exams e ON es.exam_id = e.id WHERE es.session_id = ?",
            (session_id,)
        )
        exam_session = await cursor.fetchone()
        
        return {
            "session_id": session_id,
            "session_table": {
                "exam_name": session_exam[0] if session_exam else None,
                "exam_duration": session_exam[1] if session_exam else None,
                "exam_id": session_exam[2] if session_exam else None
            },
            "exam_sessions_table": {
                "exam_id": exam_session[0] if exam_session else None,
                "exam_name": exam_session[1] if exam_session else None,
                "exam_duration": exam_session[2] if exam_session else None
            } if exam_session else None,
            "in_memory": {
                "exam_name": sessions.get(session_id, {}).get("exam_name"),
                "exam_duration": sessions.get(session_id, {}).get("exam_duration"),
                "exam_id": sessions.get(session_id, {}).get("exam_id")
            } if session_id in sessions else None
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
