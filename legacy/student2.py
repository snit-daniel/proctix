#!/usr/bin/env python3
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from datetime import datetime
import uuid
from typing import Dict, Optional, List
import json
import uvicorn
import asyncio
import base64
import io
from PIL import Image, ImageDraw, ImageFont
import mss
import mss.tools
import time
import os
import cv2
import numpy as np
import re
import sqlite3
import aiosqlite
from contextlib import asynccontextmanager
import face_recognition
import tempfile
import os
import random
from fastapi import Request


VIDEO_FOLDER = "screen_recordings"
os.makedirs(VIDEO_FOLDER, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    yield
    # Shutdown
    # Add any cleanup code here

app = FastAPI(lifespan=lifespan)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Add to your config
BANDWIDTH_OPTIMIZATION = {
    'very_low': {'fps': 3, 'quality': 30, 'resolution': '640x480'},
    'low': {'fps': 5, 'quality': 50, 'resolution': '800x600'},
    'medium': {'fps': 8, 'quality': 65, 'resolution': '1024x768'},
    'high': {'fps': 12, 'quality': 75, 'resolution': '1280x720'},
    'very_high': {'fps': 15, 'quality': 85, 'resolution': '1280x720'}
}

SCREENSHOT_SETTINGS = {
    'very_low': {'interval': 30, 'quality': 40},
    'low': {'interval': 20, 'quality': 50},
    'medium': {'interval': 15, 'quality': 60},
    'high': {'interval': 10, 'quality': 70},
    'very_high': {'interval': 5, 'quality': 80}
}

BEHAVIORAL_THRESHOLDS = {
    'mouse_straight_line_ratio': 0.3,
    'keystroke_variance': 10,
    'focus_changes_per_minute': 15
}


# Global dictionary to store remote control detectors
remote_detectors = {}

# Database setup
DATABASE_NAME = "proctoring.db"

async def init_db():
    """Initialize database with all required tables"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Create students table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS students (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                created_at TEXT
            )
        ''')
        
        # Create sessions table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT,
                student_id TEXT,
                student_name TEXT,
                exam_id TEXT,
                exam_name TEXT,
                exam_duration INTEGER,
                verification_status TEXT,
                mobile_connected BOOLEAN,
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
                review_status TEXT
            )
        ''')
        
        # Create violations table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS violations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                details TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                processed BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        ''')


        # Create behavioral_patterns table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS behavioral_patterns (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                pattern_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                details TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        ''')
        
        # Create offline_data table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS offline_data (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                data_type TEXT NOT NULL,
                encrypted_data TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                synced BOOLEAN DEFAULT FALSE,
                sync_timestamp DATETIME,
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
        

        await db.execute('''
            CREATE TABLE IF NOT EXISTS recordings (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                start_time TEXT,
                end_time TEXT,
                fps INTEGER,
                quality INTEGER,
                frame_count INTEGER,
                video_path TEXT,        -- Path to video file
                video_filename TEXT,    -- Filename
                file_size INTEGER,      -- File size in bytes
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
        
        await db.commit()

# Session storage
sessions: Dict[str, dict] = {}
mobile_connections: Dict[str, WebSocket] = {}

# Screenshot and recording state
screenshot_tasks: Dict[str, asyncio.Task] = {}
recording_tasks: Dict[str, asyncio.Task] = {}
recording_state: Dict[str, dict] = {}

# Student verification storage
student_verifications: Dict[str, dict] = {}

class ScreenshotConfig(BaseModel):
    interval: int = 5  # seconds between screenshots
    quality: int = 70  # JPEG quality (1-100)

class RecordingConfig(BaseModel):
    fps: int = 10  # frames per second
    quality: int = 50  # JPEG quality (1-100)

class VerificationRequest(BaseModel):
    student_id: str
    student_name: str


async def insert_session(session_data: dict):
    """Insert a new session into the database (with exam_id)."""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            '''INSERT OR REPLACE INTO sessions
            (id, created_at, student_id, student_name, exam_id, exam_name, exam_duration,
             verification_status, mobile_connected, mobile_status, face_detection_status,
             audio_monitoring_status, behavior_monitoring_status, verification_attempts,
             started_at, ended_at, submission_status, termination_reason, total_violations,
             severity_level, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                session_data["id"],
                session_data["created_at"],
                session_data.get("student_id"),
                session_data.get("student_name"),
                session_data.get("exam_id"),
                session_data.get("exam_name"),
                session_data.get("exam_duration"),
                session_data.get("verification_status", "pending"),
                int(session_data.get("mobile_connected", False)),   # store as int (0/1)
                session_data.get("mobile_status", "disconnected"),
                session_data.get("face_detection_status", "initializing"),
                session_data.get("audio_monitoring_status", "initializing"),
                session_data.get("behavior_monitoring_status", "initializing"),
                session_data.get("verification_attempts", 0),
                session_data.get("started_at"),
                session_data.get("ended_at"),
                session_data.get("submission_status", "not_started"),
                session_data.get("termination_reason"),
                session_data.get("total_violations", 0),
                session_data.get("severity_level", "low"),
                session_data.get("review_status", "pending")
            )
        )
        await db.commit()


async def update_session(session_id: str, updates: dict):
    """Update session information in database with better error handling"""
    if not updates:
        return
        
    try:
        async with aiosqlite.connect(DATABASE_NAME) as db:
            # Build dynamic update query
            set_parts = []
            values = []
            
            for key, value in updates.items():
                if value is not None:
                    set_parts.append(f"{key} = ?")
                    values.append(value)
            
            if not set_parts:
                return
                
            values.append(session_id)
            query = f"UPDATE sessions SET {', '.join(set_parts)} WHERE id = ?"
            
            await db.execute(query, values)
            await db.commit()
            
        # Update in-memory session if it exists
        if session_id in sessions:
            sessions[session_id].update(updates)
            
    except Exception as e:
        print(f"ERROR: Failed to update session {session_id}: {e}")



async def link_session_to_exam(session_id: str, exam_id: str):
    """Link a session to a specific exam"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            '''INSERT OR REPLACE INTO exam_sessions (exam_id, session_id, assigned_at)
            VALUES (?, ?, ?)''',
            (exam_id, session_id, datetime.now().isoformat())
        )
        await db.commit()



async def insert_violation(violation_data: dict):
    """Insert a violation into the database"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            '''INSERT INTO violations (id, session_id, type, timestamp, details)
            VALUES (?, ?, ?, ?, ?)''',
            (
                violation_data["id"],
                violation_data["session_id"],
                violation_data["type"],
                violation_data["timestamp"],
                json.dumps(violation_data["details"]) if isinstance(violation_data["details"], dict) else violation_data["details"]
            )
        )
        await db.commit()

async def insert_screenshot(screenshot_data: dict):
    """Insert a screenshot into the database"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            '''INSERT INTO screenshots (id, session_id, timestamp, image_data, format)
            VALUES (?, ?, ?, ?, ?)''',
            (
                screenshot_data["id"],
                screenshot_data["session_id"],
                screenshot_data["timestamp"],
                screenshot_data["data"],
                screenshot_data["format"]
            )
        )
        await db.commit()


async def insert_recording(recording_data: dict):
    """Insert a recording into the database"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            '''INSERT INTO recordings (id, session_id, start_time, end_time, fps, quality, frame_count, video_path, video_filename, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                recording_data["id"],
                recording_data["session_id"],
                recording_data["start_time"],
                recording_data["end_time"],
                recording_data["fps"],
                recording_data["quality"],
                recording_data["frame_count"],
                recording_data["video_path"],
                recording_data["video_filename"],
                recording_data["file_size"]
            )
        )
        await db.commit()

async def insert_id_image(session_id: str, image_data: bytes, upload_time: str):
    """Insert an ID image into the database"""
    image_id = str(uuid.uuid4())
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            '''INSERT INTO id_images (id, session_id, image_data, upload_time)
            VALUES (?, ?, ?, ?)''',
            (image_id, session_id, image_data, upload_time)
        )
        await db.commit()

async def insert_face_image(session_id: str, image_data: bytes, capture_time: str):
    """Insert a face image into the database"""
    image_id = str(uuid.uuid4())
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            '''INSERT INTO face_images (id, session_id, image_data, capture_time)
            VALUES (?, ?, ?, ?)''',
            (image_id, session_id, image_data, capture_time)
        )
        await db.commit()



def preprocess_image(image_data: bytes):
    try:
        nparr = np.frombuffer(image_data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if image is None:
            return image_data
            
        # Resize to a consistent size
        image = cv2.resize(image, (640, 480))
        
        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Apply histogram equalization
        gray = cv2.equalizeHist(gray)
        
        # Apply CLAHE for adaptive histogram equalization with larger grid
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(12, 12))  # Increased parameters
        gray = clahe.apply(gray)
        
        # Apply Gaussian blur to reduce noise
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Convert back to BGR
        result_image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        
        # Enhance contrast more aggressively
        result_image = cv2.convertScaleAbs(result_image, alpha=1.3, beta=10)  # Increased contrast
        
        success, encoded_image = cv2.imencode('.jpg', result_image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        
        if success:
            return encoded_image.tobytes()
        else:
            return image_data
            
    except Exception as e:
        print(f"Error preprocessing image: {e}")
        return image_data



def detect_faces(image_data: bytes, is_id_card=False):
    """
    Detect faces in an image using face_recognition for both ID and live images.
    Returns (found: bool, count: int)
    """
    try:
        nparr = np.frombuffer(image_data, np.uint8)
        image_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image_bgr is None:
            return False, 0

        # face_recognition expects RGB
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

        # Detect all face locations
        # model="hog" is fast; use "cnn" if you have GPU and want extra accuracy
        boxes = face_recognition.face_locations(image_rgb, model="hog")

        # Optionally filter by relative size if you want to reject tiny detections.
        valid_boxes = []
        for top, right, bottom, left in boxes:
            h = bottom - top
            w = right - left
            face_ratio_w = w / image_bgr.shape[1]
            face_ratio_h = h / image_bgr.shape[0]
            if 0.05 <= face_ratio_w <= 0.8 and 0.05 <= face_ratio_h <= 0.8:
                valid_boxes.append((top, right, bottom, left))

        return len(valid_boxes) > 0, len(valid_boxes)

    except Exception as e:
        print(f"Error detecting faces: {e}")
        return False, 0



def compare_faces_enhanced(id_image_data: bytes, face_image_data: bytes, tolerance=0.5):
    """
    Enhanced face comparison with better error handling and fallback mechanisms
    """
    try:
        # Save bytes to temporary files
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp1:
            tmp1.write(id_image_data)
            img1_path = tmp1.name
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp2:
            tmp2.write(face_image_data)
            img2_path = tmp2.name
        
        try:
            # Load images
            image1 = face_recognition.load_image_file(img1_path)
            image2 = face_recognition.load_image_file(img2_path)
            
            # Get face encodings for both images
            encodings1 = face_recognition.face_encodings(image1)
            encodings2 = face_recognition.face_encodings(image2)
            
            # If no faces detected in either image, return False
            if not encodings1 or not encodings2:
                print("No faces detected in one or both images")
                return False
            
            # Use the first face found in each image (most prominent)
            encoding1 = encodings1[0]
            encoding2 = encodings2[0]
            
            # Compare faces
            face_distance = face_recognition.face_distance([encoding1], encoding2)[0]
            
            print(f"Face distance: {face_distance}, Tolerance: {tolerance}")
            
            # Return comparison result
            return face_distance < tolerance
            
        except Exception as e:
            print(f"Face recognition error: {e}")
            # Fallback to simple image comparison
            return compare_images_simple(id_image_data, face_image_data)
        finally:
            # Clean up temporary files
            try:
                os.unlink(img1_path)
                os.unlink(img2_path)
            except:
                pass
                
    except Exception as e:
        print(f"Error in compare_faces_enhanced: {e}")
        # Final fallback
        return compare_images_simple(id_image_data, face_image_data)

def enhanced_image_similarity_check(image1_data: bytes, image2_data: bytes):
    """
    Additional image similarity checks to prevent false positives
    """
    try:
        # Convert bytes to numpy arrays
        nparr1 = np.frombuffer(image1_data, np.uint8)
        nparr2 = np.frombuffer(image2_data, np.uint8)
        
        img1 = cv2.imdecode(nparr1, cv2.IMREAD_COLOR)
        img2 = cv2.imdecode(nparr2, cv2.IMREAD_COLOR)
        
        if img1 is None or img2 is None:
            return False
        
        # Resize images to same dimensions
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
        
        # Convert to grayscale
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
        
        # Calculate structural similarity with stricter parameters
        from skimage.metrics import structural_similarity as ssim
        score, _ = ssim(gray1, gray2, full=True, win_size=7, 
                       gradient=False, data_range=gray1.max() - gray1.min())
        
        # Calculate histogram correlation as additional check
        hist1 = cv2.calcHist([gray1], [0], None, [256], [0, 256])
        hist2 = cv2.calcHist([gray2], [0], None, [256], [0, 256])
        hist_corr = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
        
        print(f"SSIM Score: {score:.3f}, Histogram Correlation: {hist_corr:.3f}")
        
        # Require both similarity measures to be high
        return score > 0.65 and hist_corr > 0.7
        
    except Exception as e:
        print(f"Image similarity check error: {e}")
        return False


def check_image_quality(image_data: bytes):
    """Check if image meets quality standards for verification with relaxed thresholds"""
    try:
        nparr = np.frombuffer(image_data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if image is None:
            return False, "Invalid image"
        
        # Check image dimensions
        if image.shape[0] < 150 or image.shape[1] < 150:
            return False, "Image too small (min 150x150 pixels)"
        
        # Convert to grayscale for some checks
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Check brightness (relaxed thresholds)
        brightness = np.mean(gray)
        if brightness < 30 or brightness > 230:  # More lenient thresholds
            return False, f"Poor lighting (brightness: {brightness:.1f})"
        
        # Check contrast (relaxed threshold)
        contrast = np.std(gray)
        if contrast < 25:  # Reduced from 30
            return False, f"Low contrast (contrast: {contrast:.1f})"
        
        # Check blurriness (relaxed threshold)
        blur = cv2.Laplacian(gray, cv2.CV_64F).var()
        if blur < 30:  # Reduced from 50
            return False, f"Image too blurry (sharpness: {blur:.1f})"
        
        return True, "Good quality"
        
    except Exception as e:
        return False, f"Quality check error: {str(e)}"



@app.post("/api/sessions/{session_id}/track-actual-mouse")
async def track_actual_mouse(session_id: str, mouse_data: dict):
    """Receive actual mouse input events from frontend"""
    if session_id not in remote_detectors:
        return {"status": "detector_not_active"}
    
    await remote_detectors[session_id].track_actual_mouse_input(mouse_data)
    
    return {"status": "actual_mouse_tracked"}

@app.post("/api/sessions/{session_id}/analyze-screenshot")
async def analyze_screenshot_for_cursor(session_id: str, screenshot_data: dict):
    """Analyze screenshot for cursor movement detection"""
    if session_id not in remote_detectors:
        return {"status": "detector_not_active"}
    
    cursor_position = await remote_detectors[session_id].analyze_screenshot(
        base64.b64decode(screenshot_data['image_data'])
    )
    
    return {
        "status": "screenshot_analyzed",
        "cursor_position": cursor_position
    }

@app.get("/api/exams/{exam_id}")
async def get_exam_details(exam_id: str):
    """Get detailed information about a specific exam"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT id, name, duration, description, enable_face_detection, "
            "enable_audio_monitoring, enable_behavior_monitoring, enable_screen_capture, "
            "enable_mobile_monitoring, max_verification_attempts, max_violations, created_at "
            "FROM exams WHERE id = ? AND is_active = 1",
            (exam_id,)
        )
        exam_row = await cursor.fetchone()
        
        if not exam_row:
            raise HTTPException(status_code=404, detail="Exam not found")
        
        return {
            "id": exam_row[0],
            "name": exam_row[1],
            "duration": exam_row[2],
            "description": exam_row[3],
            "settings": {
                "enable_face_detection": bool(exam_row[4]),
                "enable_audio_monitoring": bool(exam_row[5]),
                "enable_behavior_monitoring": bool(exam_row[6]),
                "enable_screen_capture": bool(exam_row[7]),
                "enable_mobile_monitoring": bool(exam_row[8]),
                "max_verification_attempts": exam_row[9],
                "max_violations": exam_row[10]
            },
            "created_at": exam_row[11]
        }
    



@app.get("/api/sessions/{session_id}/verification-audit")
async def verification_audit(session_id: str):
    """Comprehensive verification audit"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Check session exists
        cursor = await db.execute(
            "SELECT verification_status, verification_attempts FROM sessions WHERE id = ?",
            (session_id,)
        )
        session_result = await cursor.fetchone()
        
        if not session_result:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Check ID images
        cursor = await db.execute(
            "SELECT COUNT(*), MAX(upload_time) FROM id_images WHERE session_id = ?",
            (session_id,)
        )
        id_result = await cursor.fetchone()
        
        # Check face images
        cursor = await db.execute(
            "SELECT COUNT(*), MAX(capture_time) FROM face_images WHERE session_id = ?",
            (session_id,)
        )
        face_result = await cursor.fetchone()
        
        # Check if images exist and are recent
        id_exists = id_result[0] > 0 if id_result else False
        face_exists = face_result[0] > 0 if face_result else False
        
        return {
            "session_id": session_id,
            "verification_status": session_result[0],
            "verification_attempts": session_result[1],
            "id_images_count": id_result[0] if id_result else 0,
            "id_last_upload": id_result[1] if id_result else None,
            "face_images_count": face_result[0] if face_result else 0,
            "face_last_capture": face_result[1] if face_result else None,
            "images_exist": id_exists and face_exists,
            "images_recent": check_recent_timestamp(id_result[1] if id_result else None) and 
                            check_recent_timestamp(face_result[1] if face_result else None)
        }

def check_recent_timestamp(timestamp: str, max_minutes: int = 5) -> bool:
    """Check if timestamp is recent"""
    if not timestamp:
        return False
    
    try:
        timestamp_dt = datetime.fromisoformat(timestamp)
        now = datetime.now()
        return (now - timestamp_dt).total_seconds() < (max_minutes * 60)
    except:
        return False

def compare_faces_face_recognition(id_image_data: bytes, face_image_data: bytes, tolerance=0.45):
    """
    Compare faces using face_recognition library with proper error handling
    """
    try:
        # Save bytes to temporary files
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp1:
            tmp1.write(id_image_data)
            img1_path = tmp1.name
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp2:
            tmp2.write(face_image_data)
            img2_path = tmp2.name
        
        try:
            # Load images
            image1 = face_recognition.load_image_file(img1_path)
            image2 = face_recognition.load_image_file(img2_path)
            
            # Get face encodings for both images
            encodings1 = face_recognition.face_encodings(image1)
            encodings2 = face_recognition.face_encodings(image2)
            
            # If no faces detected in either image, return False
            if not encodings1 or not encodings2:
                print("No faces detected in one or both images")
                return False
            
            # Use the first face found in each image (most prominent)
            encoding1 = encodings1[0]
            encoding2 = encodings2[0]
            
            # Compare faces
            face_distance = face_recognition.face_distance([encoding1], encoding2)[0]
            
            print(f"Face distance: {face_distance}, Tolerance: {tolerance}")
            
            # Return comparison result
            return face_distance < tolerance
            
        except Exception as e:
            print(f"Face recognition error: {e}")
            # Fallback to simple image comparison
            return compare_images_simple(id_image_data, face_image_data)
        finally:
            # Clean up temporary files
            try:
                os.unlink(img1_path)
                os.unlink(img2_path)
            except:
                pass
                
    except Exception as e:
        print(f"Error in compare_faces_face_recognition: {e}")
        # Final fallback
        return compare_images_simple(id_image_data, face_image_data)


def enhanced_face_verification(id_image_data: bytes, face_image_data: bytes, strict_mode=False):  # Changed to non-strict mode
    # Preprocess both images
    processed_id = preprocess_image(id_image_data)
    processed_face = preprocess_image(face_image_data)
    
    results = {
        "face_recognition": None,
        "structural_similarity": None,
        "histogram_correlation": None,
        "image_quality": {},
        "face_count_check": None,
        "final_confidence": 0.0
    }
    
    # 1. Check image quality (relaxed thresholds)
    id_quality_ok, id_quality_msg = check_image_quality(processed_id)
    face_quality_ok, face_quality_msg = check_image_quality(processed_face)
    
    results["image_quality"] = {
        "id_quality": id_quality_ok,
        "id_quality_msg": id_quality_msg,
        "face_quality": face_quality_ok,
        "face_quality_msg": face_quality_msg
    }
    
    # 2. Check face count (relaxed for non-strict mode)
    id_has_face, id_face_count = detect_faces(id_image_data, is_id_card=True)
    face_has_face, face_face_count = detect_faces(face_image_data, is_id_card=False)
    
    results["face_count_check"] = {
        "id_face_count": id_face_count,
        "face_face_count": face_face_count
    }
    
    # For non-strict mode, allow multiple faces or no faces to proceed
    if strict_mode:
        if id_face_count != 1:
            return False, 0.0, results
        if face_face_count != 1:
            return False, 0.0, results
    
    # 3. Multiple comparison techniques
    comparison_results = []
    
    try:
        # Primary: Face recognition with more lenient tolerance
        face_recognition_match = compare_faces_face_recognition(id_image_data, face_image_data, tolerance=0.45)
        comparison_results.append(1.0 if face_recognition_match else 0.0)
        results["face_recognition"] = face_recognition_match
        
        # Secondary: Structural similarity
        ssim_score = calculate_structural_similarity(processed_id, processed_face)
        comparison_results.append(ssim_score)
        results["structural_similarity"] = ssim_score
        
        # Tertiary: Histogram correlation
        hist_corr = calculate_histogram_correlation(processed_id, processed_face)
        comparison_results.append(hist_corr)
        results["histogram_correlation"] = hist_corr
        
    except Exception as e:
        print(f"Comparison error: {e}")
        return False, 0.0, results
    
    # 4. Calculate weighted confidence score with more weight on face recognition
    weights = [0.8, 0.15, 0.05]  # Increased weight for face recognition
    weighted_sum = sum(score * weight for score, weight in zip(comparison_results, weights))
    results["final_confidence"] = weighted_sum
    
    # 5. Determine final result with lower threshold
    match_threshold = 0.6  # Reduced from 0.57
    
    # Require face recognition match OR high overall confidence
    is_match = face_recognition_match and (weighted_sum >= match_threshold)
    
    return is_match, weighted_sum, results


def calculate_structural_similarity(image1_data: bytes, image2_data: bytes):
    """Calculate structural similarity index between two images"""
    try:
        # Convert bytes to numpy arrays
        nparr1 = np.frombuffer(image1_data, np.uint8)
        nparr2 = np.frombuffer(image2_data, np.uint8)
        
        img1 = cv2.imdecode(nparr1, cv2.IMREAD_COLOR)
        img2 = cv2.imdecode(nparr2, cv2.IMREAD_COLOR)
        
        if img1 is None or img2 is None:
            return 0.0
        
        # Resize images to same dimensions
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
        
        # Convert to grayscale
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
        
        # Calculate structural similarity
        from skimage.metrics import structural_similarity as ssim
        score, _ = ssim(gray1, gray2, full=True)
        
        return max(0.0, min(1.0, score))
        
    except Exception as e:
        print(f"Structural similarity error: {e}")
        return 0.0

def calculate_histogram_correlation(image1_data: bytes, image2_data: bytes):
    """Calculate histogram correlation between two images"""
    try:
        # Convert bytes to numpy arrays
        nparr1 = np.frombuffer(image1_data, np.uint8)
        nparr2 = np.frombuffer(image2_data, np.uint8)
        
        img1 = cv2.imdecode(nparr1, cv2.IMREAD_COLOR)
        img2 = cv2.imdecode(nparr2, cv2.IMREAD_COLOR)
        
        if img1 is None or img2 is None:
            return 0.0
        
        # Resize images to same dimensions
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
        
        # Convert to grayscale
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
        
        # Calculate histogram correlation
        hist1 = cv2.calcHist([gray1], [0], None, [256], [0, 256])
        hist2 = cv2.calcHist([gray2], [0], None, [256], [0, 256])
        
        # Normalize histograms
        hist1 = cv2.normalize(hist1, hist1).flatten()
        hist2 = cv2.normalize(hist2, hist2).flatten()
        
        # Calculate correlation
        correlation = np.corrcoef(hist1, hist2)[0, 1]
        
        return max(0.0, min(1.0, (correlation + 1) / 2))  # Convert from [-1,1] to [0,1]
        
    except Exception as e:
        print(f"Histogram correlation error: {e}")
        return 0.0

def check_liveness(face_image_data: bytes):
    """
    Basic liveness detection to prevent photo spoofing
    Returns a score between 0 (likely spoof) and 1 (likely live)
    """
    try:
        nparr = np.frombuffer(face_image_data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if image is None:
            return 0.5  # Neutral score if we can't process
        
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # 1. Check for high contrast edges (photos of photos often have strong edges)
        edges = cv2.Canny(gray, 100, 200)
        edge_density = np.sum(edges > 0) / (image.shape[0] * image.shape[1])
        
        # 2. Check for color diversity (live photos tend to have more color variation)
        if len(image.shape) == 3:  # Color image
            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            saturation = np.std(hsv[:, :, 1])
            value = np.std(hsv[:, :, 2])
            color_diversity = (saturation + value) / 2
        else:
            color_diversity = 0
        
        # 3. Check for noise pattern (live photos may have more noise)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        
        # Normalize features and combine
        edge_score = 1.0 - min(1.0, edge_density * 5)  # High edge density = lower score
        color_score = min(1.0, color_diversity / 30)    # Higher diversity = higher score
        noise_score = min(1.0, laplacian_var / 1000)    # Moderate noise = higher score
        
        # Weighted combination
        liveness_score = (edge_score * 0.4 + color_score * 0.3 + noise_score * 0.3)
        
        return max(0.0, min(1.0, liveness_score))
        
    except Exception as e:
        print(f"Liveness check error: {e}")
        return 0.5  # Neutral score on error



def compare_images_simple(image1_data: bytes, image2_data: bytes):
    """
    Simple image comparison using OpenCV for basic verification
    This is a fallback method when face_recognition library is not available
    """
    try:
        # Convert bytes to numpy arrays
        nparr1 = np.frombuffer(image1_data, np.uint8)
        nparr2 = np.frombuffer(image2_data, np.uint8)
        
        img1 = cv2.imdecode(nparr1, cv2.IMREAD_COLOR)
        img2 = cv2.imdecode(nparr2, cv2.IMREAD_COLOR)
        
        if img1 is None or img2 is None:
            return False
        
        # Resize images to same dimensions for comparison
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
        
        # Convert to grayscale
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
        
        # Calculate structural similarity
        from skimage.metrics import structural_similarity as ssim
        score, _ = ssim(gray1, gray2, full=True)
        
        # If images are very similar, they might be the same
        return score > 0.6
        
    except Exception as e:
        print(f"Simple image comparison error: {e}")
        return False


async def calculate_session_metrics(session_id: str):
    """Calculate and update dynamic session metrics from database"""
    # Get violation count from database
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM violations WHERE session_id = ?",
            (session_id,)
        )
        violation_count = (await cursor.fetchone())[0]
        
        # Get remote control specific violations for termination logic
        cursor = await db.execute(
            "SELECT COUNT(*) FROM violations WHERE session_id = ? AND type IN ('non_keyboard_input', 'high_remote_control_suspicion', 'remote_control_suspicion')",
            (session_id,)
        )
        remote_control_count = (await cursor.fetchone())[0]
    
    # Calculate severity based on violation count
    severity = "low"
    if violation_count >= 5:
        severity = "critical"
    elif violation_count >= 3:
        severity = "high"
    elif violation_count >= 1:
        # Check if any high-risk violations exist
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT type FROM violations WHERE session_id = ? AND type IN ('multiple_monitors', 'mobile_phone', 'second_person', 'non_keyboard_input', 'high_remote_control_suspicion')",
                (session_id,)
            )
            high_risk_violations = await cursor.fetchall()
            
        if high_risk_violations:
            severity = "medium"
    
    await update_session(session_id, {
        "total_violations": violation_count,
        "severity_level": severity
    })
    
    # Check for automatic termination conditions
    if violation_count >= 5:
        await update_exam_end(session_id, "terminated", f"Exceeded maximum violations (5)")
    elif remote_control_count >= 2:
        await update_exam_end(session_id, "terminated", "Repeated remote control violations detected")


    
    # Check for automatic termination conditions
    if violation_count >= 5:
        await update_exam_end(session_id, "terminated", f"Exceeded maximum violations (5)")
    elif remote_control_count >= 2:
        await update_exam_end(session_id, "terminated", "Repeated remote control violations detected")



async def update_exam_start(session_id: str, exam_name: str, duration_minutes: int):
    """Set exam details when it actually starts - without restarting recording"""
    started_at = datetime.now().isoformat()
    
    # Update database
    await update_session(session_id, {
        "started_at": started_at,
        "submission_status": "in_progress",
        "exam_name": exam_name,
        "exam_duration": duration_minutes,
        # Only update face and audio status if they're enabled
        "face_detection_status": "active",  
        "audio_monitoring_status": "active",
        "behavior_monitoring_status": "active"  # Already active from recording
    })
    
    # Also update the in-memory session
    if session_id in sessions:
        sessions[session_id].update({
            "started_at": started_at,
            "submission_status": "in_progress",
            "exam_name": exam_name,
            "exam_duration": duration_minutes,
            "face_detection_status": "active",
            "audio_monitoring_status": "active", 
            "behavior_monitoring_status": "active"
        })
    
    # Start monitoring features (but NOT recording - it's already running)
    exam_settings = sessions[session_id].get("exam_settings", {})
    
    # Start face detection if enabled
    if exam_settings.get("enable_face_detection", True):
        screenshot_config = ScreenshotConfig(interval=300, quality=70)
        await start_screenshots_internal(session_id, screenshot_config)
    
    # Start audio monitoring if enabled (recording is already running from session start)
    if exam_settings.get("enable_audio_monitoring", True):
        await start_audio_monitoring_internal(session_id)
    
    print(f"Exam started for session {session_id}. Recording continues from session start.")




@app.post("/api/sessions/{session_id}/update-status")
async def update_feature_status(session_id: str, status_update: dict):
    """API endpoint to update feature statuses"""
    feature = status_update.get("feature")
    status = status_update.get("status")
    message = status_update.get("message", "")
    
    update_fields = {}
    
    if feature == "face_detection":
        update_fields["face_detection_status"] = status
    elif feature == "audio_monitoring":
        update_fields["audio_monitoring_status"] = status
    elif feature == "behavior_monitoring":
        update_fields["behavior_monitoring_status"] = status
    elif feature == "mobile_monitoring":
        update_fields["mobile_status"] = status
        update_fields["mobile_connected"] = (status == "connected")
    
    if update_fields:
        await update_session(session_id, update_fields)
    
    return {"status": "updated", "feature": feature, "new_status": status}




async def update_exam_end(session_id: str, status: str, reason: str = ""):
    """Update session when exam ends"""
    # Get the current time
    ended_at = datetime.now().isoformat()
    
    await update_session(session_id, {
        "ended_at": ended_at,
        "submission_status": status,
        "termination_reason": reason
    })
    
    # Also update the in-memory session
    if session_id in sessions:
        sessions[session_id].update({
            "ended_at": ended_at,
            "submission_status": status,
            "termination_reason": reason
        })


@app.websocket("/ws/mobile/{session_id}")
async def mobile_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    
    # Check if session exists in database
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not await cursor.fetchone():
            await websocket.send_json({
                "type": "error",
                "message": "Invalid session ID"
            })
            await websocket.close(code=1008, reason="Session not found")
            return
    
    # Register the mobile connection
    mobile_connections[session_id] = websocket
    
    # Update database with proper status
    await update_session(session_id, {
        "mobile_connected": True,
        "mobile_status": "connected"
    })
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                
                if message["type"] == "heartbeat":
                    # Update last heartbeat time
                    await update_session(session_id, {
                        "mobile_status": "connected"
                    })
                    
                    await websocket.send_json({
                        "type": "heartbeat_response",
                        "timestamp": str(datetime.now())
                    })
                    
                elif message["type"] == "status_update":
                    # Update mobile status
                    if "status" in message:
                        await update_session(session_id, {
                            "mobile_status": message["status"]
                        })
                        
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON format"
                })
                
    except WebSocketDisconnect:
        # Clean up on disconnect
        if session_id in mobile_connections:
            del mobile_connections[session_id]
        
        # Update database
        await update_session(session_id, {
            "mobile_connected": False,
            "mobile_status": "disconnected"
        })





# Add to your FastAPI app
@app.websocket("/ws/screen-monitoring/{session_id}")
async def screen_monitoring_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    
    try:
        while True:
            # Receive screen monitoring data
            data = await websocket.receive_json()
            
            if data['type'] == 'window_analysis':
                await analyze_window_behavior(session_id, data['data'])
                
            elif data['type'] == 'tab_analysis':
                await analyze_tab_behavior(session_id, data['data'])
                
    except WebSocketDisconnect:
        print(f"Screen monitoring disconnected for session {session_id}")




@app.websocket("/ws/remote-detection/{session_id}")
async def remote_detection_websocket(websocket: WebSocket, session_id: str):  # Fix parameter name
    await websocket.accept()
    
    # Initialize detector for this session
    if session_id not in remote_detectors:
        remote_detectors[session_id] = RemoteControlDetector(session_id)
        await remote_detectors[session_id].start_detection()
    
    try:
        while True:
            data = await websocket.receive_json()
            
            # Handle different types of interaction data
            if data['type'] == 'user_interaction':
                await remote_detectors[session_id].track_user_interaction(
                    data['event_type'], **data.get('data', {})
                )
                
            elif data['type'] == 'evaluate_suspicion':
                score = await remote_detectors[session_id].evaluate_suspicion()
                await websocket.send_json({
                    'type': 'suspicion_score',
                    'score': score,
                    'non_keyboard_violations': remote_detectors[session_id].non_keyboard_monitor.violation_count,
                    'exam_terminated': remote_detectors[session_id].non_keyboard_monitor.exam_terminated
                })
                
    except WebSocketDisconnect:
        if session_id in remote_detectors:
            del remote_detectors[session_id]




@app.middleware("http")
async def verification_middleware(request: Request, call_next):
    """Stricter middleware to enforce verification"""
    if request.method == "POST" and "/start-exam" in str(request.url):
        path_parts = request.url.path.split("/")
        session_id = None
        
        try:
            session_id_index = path_parts.index("sessions") + 1
            session_id = path_parts[session_id_index]
        except (ValueError, IndexError):
            pass
        
        if session_id:
            # STRICTER verification check
            async with aiosqlite.connect(DATABASE_NAME) as db:
                # Check session verification status
                cursor = await db.execute(
                    "SELECT verification_status, student_name FROM sessions WHERE id = ?",
                    (session_id,)
                )
                session_result = await cursor.fetchone()
                
                if not session_result or session_result[0] != "completed":
                    return JSONResponse(
                        status_code=403,
                        content={
                            "status": "error",
                            "message": "Identity verification not completed"
                        }
                    )
                
                # Additional check: verify images exist and are recent
                cursor = await db.execute(
                    """SELECT 
                        (SELECT COUNT(*) FROM id_images WHERE session_id = ?) as id_count,
                        (SELECT COUNT(*) FROM face_images WHERE session_id = ?) as face_count,
                        (SELECT MAX(upload_time) FROM id_images WHERE session_id = ?) as id_time,
                        (SELECT MAX(capture_time) FROM face_images WHERE session_id = ?) as face_time""",
                    (session_id, session_id, session_id, session_id)
                )
                image_result = await cursor.fetchone()
                
                id_count, face_count, id_time, face_time = image_result
                
                # Require both images and recent timestamps
                if id_count == 0 or face_count == 0:
                    return JSONResponse(
                        status_code=403,
                        content={
                            "status": "error", 
                            "message": "ID and face verification images required"
                        }
                    )
                
                if not check_recent_timestamp(id_time) or not check_recent_timestamp(face_time):
                    return JSONResponse(
                        status_code=403,
                        content={
                            "status": "error",
                            "message": "Verification images expired. Please re-verify."
                        }
                    )
    
    response = await call_next(request)
    return response



async def analyze_window_behavior(session_id: str, window_data: dict):
    """Analyze window behavior for suspicious activity"""
    suspicious_patterns = []
    
    # Detect floating browsers (small, always-on-top windows)
    if (window_data['width'] < 400 and window_data['height'] < 300 and 
        window_data['alwaysOnTop'] and 'browser' in window_data['title'].lower()):
        suspicious_patterns.append('floating_browser')
    
    # Detect rapid window switching
    if window_data['focusChanges'] > 10:  # More than 10 changes per minute
        suspicious_patterns.append('excessive_focus_changes')
    
    # Detect off-screen windows
    if (window_data['x'] + window_data['width'] < 0 or 
        window_data['y'] + window_data['height'] < 0):
        suspicious_patterns.append('offscreen_window')
    
    if suspicious_patterns:
        await record_violation(session_id, Violation(
            type="suspicious_window_behavior",
            details={
                "patterns": suspicious_patterns,
                "window_data": window_data,
                "timestamp": datetime.now().isoformat()
            }
        ))



async def start_recording_internal(session_id: str, config: RecordingConfig):
    """Internal function to start recording without HTTP endpoint constraints"""
    # Remove the session existence check - we'll work directly with database
    
    # Stop existing recording task if any
    if session_id in recording_tasks:
        recording_tasks[session_id].cancel()
        if session_id in recording_state:
            recording_state[session_id]["recording"] = False
    
    # Start new recording task
    recording_tasks[session_id] = asyncio.create_task(
        record_screen(session_id, config.fps, config.quality)
    )
    
    # Update database ONLY - no in-memory session needed
    await update_session(session_id, {
        "behavior_monitoring_status": "active"
    })

async def start_screenshots_internal(session_id: str, config: ScreenshotConfig):
    """Internal function to start screenshots without HTTP endpoint constraints"""
    # Remove the session existence check
    
    # Stop existing screenshot task if any
    if session_id in screenshot_tasks:
        screenshot_tasks[session_id].cancel()
    
    # Start new screenshot task
    screenshot_tasks[session_id] = asyncio.create_task(
        take_screenshots(session_id, config.interval, config.quality)
    )
    
    # Update database ONLY
    await update_session(session_id, {
        "face_detection_status": "active"
    })

async def start_audio_monitoring_internal(session_id: str):
    """Internal function to start audio monitoring"""
    # Update database ONLY
    await update_session(session_id, {
        "audio_monitoring_status": "active"
    })


@app.post("/api/sessions")
async def create_session(exam_data: dict = None):
    """Create a new session and start recording immediately"""
    session_id = str(uuid.uuid4())
    created_at = str(datetime.now())
    
    # Extract exam data if provided
    exam_id = exam_data.get("exam_id") if exam_data else None
    exam_name = "Unknown Exam"
    exam_duration = 60
    exam_settings = {
        "enable_face_detection": True,
        "enable_audio_monitoring": True, 
        "enable_behavior_monitoring": True,
        "enable_screen_capture": True,
        "enable_mobile_monitoring": False,
        "max_violations": 5,
        "max_verification_attempts": 3
    }
    
    # Get exam-specific settings if exam_id is provided
    if exam_id:
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT name, duration, enable_face_detection, enable_audio_monitoring, "
                "enable_behavior_monitoring, enable_screen_capture, enable_mobile_monitoring, "
                "max_verification_attempts, max_violations FROM exams WHERE id = ?",
                (exam_id,)
            )
            exam_row = await cursor.fetchone()
            if exam_row:
                exam_name = exam_row[0]
                exam_duration = exam_row[1]
                exam_settings = {
                    "enable_face_detection": bool(exam_row[2]),
                    "enable_audio_monitoring": bool(exam_row[3]),
                    "enable_behavior_monitoring": bool(exam_row[4]),
                    "enable_screen_capture": bool(exam_row[5]),
                    "enable_mobile_monitoring": bool(exam_row[6]),
                    "max_verification_attempts": exam_row[7],
                    "max_violations": exam_row[8]
                }
    
    # Create session data
    session_data = {
        "id": session_id,
        "created_at": created_at,
        "violations": [],
        "mobile_connected": False,
        "mobile_status": "disconnected",
        "face_detection_status": "initializing",
        "audio_monitoring_status": "initializing", 
        "behavior_monitoring_status": "initializing",
        "screenshots": [],
        "recordings": [],
        "verification_status": "pending",
        "verification_attempts": 0,
        "student_id": None,
        "student_name": None,
        "exam_name": exam_name,
        "exam_duration": exam_duration,
        "exam_id": exam_id,
        "started_at": None,
        "ended_at": None,
        "submission_status": "not_started",
        "termination_reason": None,
        "total_violations": 0,
        "severity_level": "low",
        "review_status": "pending",
        "exam_settings": exam_settings
    }
    
    # Store in memory
    sessions[session_id] = session_data
    
    # Insert into database
    await insert_session(session_data)

    # 🚀 START RECORDING IMMEDIATELY AFTER SESSION CREATION 🚀
    if exam_settings.get("enable_screen_capture", True):
        try:
            recording_config = RecordingConfig(fps=10, quality=50)
            await start_recording_immediately(session_id, recording_config)
            print(f"✅ Recording started immediately for session {session_id}")
        except Exception as e:
            print(f"❌ Failed to start recording immediately: {e}")

    return {
        "session_id": session_id,
        "created_at": session_data["created_at"],
        "status": "created",
        "exam_name": session_data["exam_name"],
        "exam_duration": session_data["exam_duration"],
        "exam_id": session_data["exam_id"],
        "exam_settings": session_data["exam_settings"],
        "recording_started": exam_settings.get("enable_screen_capture", True)
    }




class Violation(BaseModel):
    type: str
    details: dict = {}


class MouseMovementDetector:
    """Detect when mouse cursor moves without local mouse input events"""
    
    def __init__(self, session_id):
        self.session_id = session_id
        self.actual_mouse_events = []  # Real mouse input events from system
        self.cursor_positions = []     # Cursor positions from screen analysis
        self.last_cursor_position = None
        self.detection_started = False
        self.screenshot_analyzer = ScreenshotAnalyzer(session_id)
        
    async def start_detection(self):
        """Start mouse movement detection"""
        self.detection_started = True
        # Start analyzing screenshots for cursor movement
        await self.screenshot_analyzer.start_analysis()
        print(f"Mouse movement detection started for session {self.session_id}")
    
    async def track_actual_mouse_input(self, mouse_event):
        """Track actual mouse input events from the local system"""
        event_data = {
            'timestamp': time.time() * 1000,
            'type': mouse_event.get('type', 'unknown'),
            'x': mouse_event.get('x', 0),
            'y': mouse_event.get('y', 0),
            'movementX': mouse_event.get('movementX', 0),
            'movementY': mouse_event.get('movementY', 0),
            'source': 'local_system'
        }
        
        self.actual_mouse_events.append(event_data)
        
        # Keep only recent events (last 30 seconds)
        current_time = time.time() * 1000
        self.actual_mouse_events = [e for e in self.actual_mouse_events 
                                  if current_time - e['timestamp'] < 30000]
    
    async def track_cursor_position(self, cursor_data):
        """Track cursor position from screenshot analysis"""
        position_data = {
            'timestamp': time.time() * 1000,
            'x': cursor_data.get('x', 0),
            'y': cursor_data.get('y', 0),
            'detected_from': 'screenshot_analysis',
            'confidence': cursor_data.get('confidence', 0)
        }
        
        self.cursor_positions.append(position_data)
        
        # Analyze for discrepancies
        await self.analyze_movement_discrepancy(position_data)
        
        # Keep only recent positions
        current_time = time.time() * 1000
        self.cursor_positions = [p for p in self.cursor_positions 
                               if current_time - p['timestamp'] < 30000]
    
    async def analyze_movement_discrepancy(self, current_cursor_position):
        """Analyze if cursor moved without corresponding mouse input"""
        if not self.actual_mouse_events:
            return
        
        # Find the closest actual mouse event in time
        time_diff_threshold = 100  # milliseconds
        closest_event = None
        min_time_diff = float('inf')
        
        for event in self.actual_mouse_events:
            time_diff = abs(current_cursor_position['timestamp'] - event['timestamp'])
            if time_diff < min_time_diff:
                min_time_diff = time_diff
                closest_event = event
        
        if not closest_event or min_time_diff > time_diff_threshold:
            # No recent mouse event for this cursor position
            await self.detect_ghost_movement(current_cursor_position)
            return
        
        # Check if cursor moved significantly from the last known position
        if self.last_cursor_position:
            movement_distance = self.calculate_distance(
                self.last_cursor_position, current_cursor_position
            )
            
            # If cursor moved but no mouse event captured it
            if (movement_distance > 10 and  # Significant movement
                min_time_diff > 50):  # But no recent mouse event
                await self.detect_ghost_movement(current_cursor_position)
        
        self.last_cursor_position = current_cursor_position
    
    async def detect_ghost_movement(self, cursor_position):
        """Detect when cursor moves without local mouse input"""
        print(f"GHOST MOVEMENT DETECTED: Cursor at ({cursor_position['x']}, {cursor_position['y']}) without local mouse input")
        
        # Check if this is part of a pattern
        ghost_movements = await self.analyze_ghost_movement_pattern()
        
        if ghost_movements['count'] > 3:  # Multiple ghost movements detected
            await self.record_remote_control_violation(
                ghost_movements['count'],
                ghost_movements['pattern_confidence'],
                cursor_position
            )
    
    async def analyze_ghost_movement_pattern(self):
        """Analyze pattern of ghost movements"""
        if len(self.cursor_positions) < 5:
            return {'count': 0, 'pattern_confidence': 0}
        
        ghost_count = 0
        recent_positions = self.cursor_positions[-10:]  # Last 10 positions
        
        for i in range(1, len(recent_positions)):
            current_pos = recent_positions[i]
            prev_pos = recent_positions[i-1]
            
            # Check if movement occurred between these positions
            movement_distance = self.calculate_distance(prev_pos, current_pos)
            
            if movement_distance > 5:  # Significant movement
                # Check if there was a corresponding mouse event
                has_matching_event = await self.has_matching_mouse_event(
                    prev_pos, current_pos
                )
                
                if not has_matching_event:
                    ghost_count += 1
        
        pattern_confidence = min(1.0, ghost_count / len(recent_positions))
        
        return {
            'count': ghost_count,
            'pattern_confidence': pattern_confidence
        }
    
    async def has_matching_mouse_event(self, pos1, pos2):
        """Check if there's a mouse event that matches this cursor movement"""
        movement_time_range = (pos1['timestamp'], pos2['timestamp'])
        
        for event in self.actual_mouse_events:
            if (movement_time_range[0] <= event['timestamp'] <= movement_time_range[1]):
                # Check if event coordinates are close to cursor positions
                distance_to_pos1 = self.calculate_distance(
                    {'x': event['x'], 'y': event['y']}, pos1
                )
                distance_to_pos2 = self.calculate_distance(
                    {'x': event['x'], 'y': event['y']}, pos2
                )
                
                if distance_to_pos1 < 20 or distance_to_pos2 < 20:
                    return True
        
        return False
    
    def calculate_distance(self, point1, point2):
        """Calculate distance between two points"""
        return ((point2['x'] - point1['x'])**2 + 
                (point2['y'] - point1['y'])**2)**0.5
    
    async def record_remote_control_violation(self, ghost_count, confidence, cursor_position):
        """Record remote control violation for ghost movements"""
        violation_data = {
            "id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "type": "cursor_movement_without_input",
            "timestamp": datetime.now().isoformat(),
            "details": {
                "ghost_movement_count": ghost_count,
                "confidence_score": confidence,
                "cursor_position": {
                    "x": cursor_position['x'],
                    "y": cursor_position['y'],
                    "timestamp": cursor_position['timestamp']
                },
                "message": f"Cursor moved {ghost_count} times without local mouse input (confidence: {confidence:.1%})"
            }
        }
        
        await insert_violation(violation_data)
        await self.update_violation_count()
        
        # High confidence detection
        if confidence > 0.8:
            await self.handle_high_confidence_ghost_detection(ghost_count)
    
    async def handle_high_confidence_ghost_detection(self, ghost_count):
        """Handle high-confidence ghost movement detection"""
        violation_data = {
            "id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "type": "high_confidence_remote_cursor",
            "timestamp": datetime.now().isoformat(),
            "details": {
                "ghost_movements_detected": ghost_count,
                "severity": "critical",
                "message": "High confidence remote cursor control detected - cursor moving without local input"
            }
        }
        
        await insert_violation(violation_data)
        
        # Check for termination
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM violations WHERE session_id = ? AND type LIKE '%cursor%'",
                (self.session_id,)
            )
            cursor_violations = (await cursor.fetchone())[0]
            
            if cursor_violations >= 2:
                await update_exam_end(self.session_id, "terminated", 
                                   "Remote cursor control violations detected")
    
    async def update_violation_count(self):
        """Update violation count in session"""
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM violations WHERE session_id = ?",
                (self.session_id,)
            )
            violation_count = (await cursor.fetchone())[0]
            
            await update_session(self.session_id, {
                "total_violations": violation_count
            })



class ScreenshotAnalyzer:
    """Analyze screenshots to detect cursor position and movement"""
    
    def __init__(self, session_id):
        self.session_id = session_id
        self.previous_screenshot = None
        self.cursor_template = self.create_cursor_template()
        self.analysis_active = False
        
    def create_cursor_template(self):
        """Create templates for common cursor shapes"""
        # This would be enhanced with actual cursor image templates
        # For now, we'll use movement detection between frames
        return None
    
    async def start_analysis(self):
        """Start analyzing screenshots for cursor movement"""
        self.analysis_active = True
        print("Screenshot analysis started for cursor detection")
    
    async def analyze_screenshot(self, screenshot_data):
        """Analyze screenshot for cursor position"""
        if not self.analysis_active:
            return None
        
        try:
            # Convert screenshot data to OpenCV image
            nparr = np.frombuffer(screenshot_data, np.uint8)
            current_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if current_frame is None:
                return None
            
            cursor_position = None
            
            if self.previous_screenshot is not None:
                # Method 1: Frame difference to detect movement
                cursor_position = await self.detect_cursor_by_movement(current_frame)
            
            # Method 2: Template matching for cursor shape
            if cursor_position is None:
                cursor_position = await self.detect_cursor_by_template(current_frame)
            
            self.previous_screenshot = current_frame.copy()
            
            return cursor_position
            
        except Exception as e:
            print(f"Screenshot analysis error: {e}")
            return None
    
    async def detect_cursor_by_movement(self, current_frame):
        """Detect cursor by analyzing movement between frames"""
        try:
            # Convert to grayscale
            prev_gray = cv2.cvtColor(self.previous_screenshot, cv2.COLOR_BGR2GRAY)
            curr_gray = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
            
            # Calculate absolute difference
            diff = cv2.absdiff(prev_gray, curr_gray)
            
            # Apply threshold to highlight significant changes
            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
            
            # Find contours of changed regions
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            cursor_candidates = []
            
            for contour in contours:
                # Filter by size (cursor-sized changes)
                area = cv2.contourArea(contour)
                if 10 < area < 500:  # Cursor-sized area
                    x, y, w, h = cv2.boundingRect(contour)
                    
                    # Cursor is usually small and pointer-shaped
                    if w < 50 and h < 50 and w * h < 1000:
                        cursor_candidates.append({
                            'x': x + w // 2,
                            'y': y + h // 2,
                            'confidence': min(1.0, area / 100),
                            'method': 'movement_detection'
                        })
            
            # Return the most likely cursor position
            if cursor_candidates:
                return max(cursor_candidates, key=lambda c: c['confidence'])
            
            return None
            
        except Exception as e:
            print(f"Movement detection error: {e}")
            return None
    
    async def detect_cursor_by_template(self, current_frame):
        """Detect cursor using template matching"""
        # This would require pre-loaded cursor templates
        # For now, return None - we'll rely on movement detection
        return None
    



class BehavioralAnalyzer:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.mouse_movements = []
        self.keystroke_timings = []
        self.focus_changes = []
        self.last_analysis_time = datetime.now()

    async def analyze_behavior(self, event_type: str, event_data: dict):
        """Analyze user behavior for suspicious patterns"""
        if event_type == 'mouse_move':
            self.mouse_movements.append(event_data)
        elif event_type == 'key_press':
            self.keystroke_timings.append(event_data)
        elif event_type == 'focus_change':
            self.focus_changes.append(event_data)

        # Analyze every 30 seconds
        if (datetime.now() - self.last_analysis_time).total_seconds() > 30:
            await self.perform_behavioral_analysis()
            self.last_analysis_time = datetime.now()

    async def perform_behavioral_analysis(self):
        """Perform comprehensive behavioral analysis"""
        suspicious_patterns = []
        
        # Mouse movement analysis
        if self.detect_automated_mouse_movements():
            suspicious_patterns.append('automated_mouse_movements')
        
        # Keystroke analysis
        if self.detect_consistent_keystroke_timing():
            suspicious_patterns.append('consistent_keystroke_timing')
        
        # Focus analysis
        if self.detect_rapid_focus_changes():
            suspicious_patterns.append('rapid_focus_changes')

        if suspicious_patterns:
            await record_violation(self.session_id, Violation(
                type="suspicious_behavior_patterns",
                details={
                    "patterns": suspicious_patterns,
                    "mouse_movements": len(self.mouse_movements),
                    "keystrokes": len(self.keystroke_timings),
                    "focus_changes": len(self.focus_changes),
                    "timestamp": datetime.now().isoformat()
                }
            ))

        # Clear stored data
        self.mouse_movements = []
        self.keystroke_timings = []
        self.focus_changes = []

    def detect_automated_mouse_movements(self):
        """Detect non-human mouse patterns"""
        if len(self.mouse_movements) < 10:
            return False
        
        straight_line_count = 0
        for i in range(len(self.mouse_movements) - 1):
            movement = self.mouse_movements[i]
            next_movement = self.mouse_movements[i + 1]
            
            dx = abs(next_movement['x'] - movement['x'])
            dy = abs(next_movement['y'] - movement['y'])
            
            if dx > 0 and dy > 0 and abs(dx - dy) / max(dx, dy) < 0.1:
                straight_line_count += 1
        
        return straight_line_count / len(self.mouse_movements) > 0.3

    def detect_consistent_keystroke_timing(self):
        """Detect machine-like typing"""
        if len(self.keystroke_timings) < 20:
            return False
        
        timings = [k['timing'] for k in self.keystroke_timings]
        mean = sum(timings) / len(timings)
        variance = sum((t - mean) ** 2 for t in timings) / len(timings)
        
        return variance < 10

    def detect_rapid_focus_changes(self):
        """Detect excessive window switching"""
        return len(self.focus_changes) > 15

    


# ==================== REMOTE CONTROL DETECTION CLASSES ====================

class ResponseTimingDetector:
    def __init__(self, session_id):
        self.session_id = session_id
        self.response_times = []
        self.interaction_times = {}
        self.suspicion_score = 0
        
    async def track_interaction_start(self, interaction_type, element_id=None):
        """Track when user starts interacting with an element"""
        current_time = time.time() * 1000
        self.interaction_times[interaction_type] = {
            'start_time': current_time,
            'element_id': element_id,
            'end_time': None
        }
        
    async def track_interaction_end(self, interaction_type):
        """Track when user finishes interacting"""
        if interaction_type in self.interaction_times:
            current_time = time.time() * 1000
            start_time = self.interaction_times[interaction_type]['start_time']
            response_time = current_time - start_time
            
            self.response_times.append(response_time)
            await self.analyze_response_patterns()
            
    async def analyze_response_patterns(self):
        """Analyze response patterns for consistent latency"""
        if len(self.response_times) < 5:
            return
            
        # Calculate statistics
        avg_response = sum(self.response_times) / len(self.response_times)
        variance = sum((x - avg_response) ** 2 for x in self.response_times) / len(self.response_times)
        
        # Remote control often shows consistent timing with moderate delays
        if variance < 10000 and 800 < avg_response < 2000:
            self.suspicion_score += 0.3
            await self.record_suspicion('consistent_response_timing', {
                'average_delay': avg_response,
                'variance': variance,
                'sample_size': len(self.response_times)
            })
            
    async def record_suspicion(self, pattern_type, details):
        """Record suspicious pattern as a violation"""
        violation_data = {
            "id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "type": "remote_control_suspicion",
            "timestamp": datetime.now().isoformat(),
            "details": {
                "pattern": pattern_type,
                **details,
                "overall_suspicion_score": self.suspicion_score
            }
        }
        
        await insert_violation(violation_data)
        await self.update_violation_count()
        
    async def update_violation_count(self):
        """Update the total violation count in the session"""
        # Get current violation count from database
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM violations WHERE session_id = ?",
                (self.session_id,)
            )
            violation_count = (await cursor.fetchone())[0]
            
            # Update session
            await update_session(self.session_id, {
                "total_violations": violation_count
            })
            
            # Check if we reached the limit
            max_violations = 5  # Default
            if self.session_id in sessions and sessions[self.session_id].get("exam_settings"):
                max_violations = sessions[self.session_id]["exam_settings"].get("max_violations", 5)
            
            if violation_count >= max_violations:
                await update_exam_end(self.session_id, "terminated", f"Exceeded maximum violations ({max_violations})")

class InputPatternDetector:
    def __init__(self, session_id):
        self.session_id = session_id
        self.typing_patterns = []
        self.mouse_patterns = []
        self.baseline_established = False
        self.baseline = None
        self.suspicion_score = 0
        
    async def track_keystroke(self, key, timing, correct=True):
        """Track individual keystrokes"""
        self.typing_patterns.append({
            'key': key,
            'timing': timing,
            'correct': correct,
            'timestamp': time.time() * 1000
        })
        
        if len(self.typing_patterns) > 20 and not self.baseline_established:
            await self.establish_baseline()
            
        if self.baseline_established:
            await self.detect_input_changes()
            
    async def track_mouse_movement(self, x, y, speed, acceleration, event_type):
        """Track mouse movements"""
        self.mouse_patterns.append({
            'x': x, 'y': y, 'speed': speed, 
            'acceleration': acceleration, 'type': event_type,
            'timestamp': time.time() * 1000
        })
        
        # Keep only recent data
        if len(self.mouse_patterns) > 100:
            self.mouse_patterns = self.mouse_patterns[-100:]
            
    async def establish_baseline(self):
        """Establish baseline typing patterns"""
        if len(self.typing_patterns) < 20:
            return
            
        timings = [p['timing'] for p in self.typing_patterns]
        typing_speed = self.calculate_typing_speed()
        error_rate = self.calculate_error_rate()
        
        self.baseline = {
            'typing_speed': typing_speed,
            'error_rate': error_rate,
            'timing_variance': self.calculate_variance(timings),
            'established_at': time.time() * 1000
        }
        
        self.baseline_established = True
        
    async def detect_input_changes(self):
        """Detect changes from baseline patterns"""
        current_speed = self.calculate_typing_speed()
        current_error_rate = self.calculate_error_rate()
        current_timings = [p['timing'] for p in self.typing_patterns[-20:]]
        current_variance = self.calculate_variance(current_timings)
        
        # Check for significant deviations
        speed_change = abs(current_speed - self.baseline['typing_speed']) / self.baseline['typing_speed']
        error_change = abs(current_error_rate - self.baseline['error_rate'])
        variance_change = abs(current_variance - self.baseline['timing_variance']) / self.baseline['timing_variance']
        
        if speed_change > 0.4 or error_change > 0.3 or variance_change > 0.5:
            self.suspicion_score += 0.4
            await self.record_suspicion('input_pattern_change', {
                'speed_change': speed_change,
                'error_change': error_change,
                'variance_change': variance_change
            })
            
    def calculate_typing_speed(self):
        """Calculate words per minute"""
        if len(self.typing_patterns) < 2:
            return 0
            
        first_time = self.typing_patterns[0]['timestamp']
        last_time = self.typing_patterns[-1]['timestamp']
        total_chars = len(self.typing_patterns)
        
        time_minutes = (last_time - first_time) / 60000
        return (total_chars / 5) / time_minutes  # Assuming 5 chars per word
        
    def calculate_error_rate(self):
        """Calculate error rate"""
        if not self.typing_patterns:
            return 0
            
        errors = sum(1 for p in self.typing_patterns if not p['correct'])
        return errors / len(self.typing_patterns)
        
    def calculate_variance(self, values):
        """Calculate variance of values"""
        if len(values) < 2:
            return 0
            
        mean = sum(values) / len(values)
        return sum((x - mean) ** 2 for x in values) / len(values)
        
    async def record_suspicion(self, pattern_type, details):
        """Record suspicious pattern as a violation"""
        violation_data = {
            "id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "type": "remote_control_suspicion",
            "timestamp": datetime.now().isoformat(),
            "details": {
                "pattern": pattern_type,
                **details,
                "overall_suspicion_score": self.suspicion_score
            }
        }
        
        await insert_violation(violation_data)
        await self.update_violation_count()
        
    async def update_violation_count(self):
        """Update the total violation count in the session"""
        # Get current violation count from database
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM violations WHERE session_id = ?",
                (self.session_id,)
            )
            violation_count = (await cursor.fetchone())[0]
            
            # Update session
            await update_session(self.session_id, {
                "total_violations": violation_count
            })
            
            # Check if we reached the limit
            max_violations = 5  # Default
            if self.session_id in sessions and sessions[self.session_id].get("exam_settings"):
                max_violations = sessions[self.session_id]["exam_settings"].get("max_violations", 5)
            
            if violation_count >= max_violations:
                await update_exam_end(self.session_id, "terminated", f"Exceeded maximum violations ({max_violations})")

class FocusAnomalyDetector:
    def __init__(self, session_id):
        self.session_id = session_id
        self.gaze_history = []
        self.interaction_history = []
        self.suspicion_score = 0
        
    async def track_gaze_position(self, x, y, confidence):
        """Track gaze position"""
        self.gaze_history.append({
            'x': x, 'y': y, 'confidence': confidence,
            'timestamp': time.time() * 1000
        })
        
        # Keep only recent data
        if len(self.gaze_history) > 50:
            self.gaze_history = self.gaze_history[-50:]
            
        await self.analyze_gaze_patterns()
        
    async def track_interaction(self, interaction_type, screen_x, screen_y):
        """Track user interactions"""
        self.interaction_history.append({
            'type': interaction_type, 'screen_x': screen_x, 'screen_y': screen_y,
            'timestamp': time.time() * 1000
        })
        
        await self.analyze_gaze_interaction_correlation()
        
    async def analyze_gaze_patterns(self):
        """Analyze gaze patterns for anomalies"""
        if not self.interaction_history or not self.gaze_history:
            return
            
        recent_interaction = self.interaction_history[-1]
        gaze_around_interaction = self.get_gaze_around_time(recent_interaction['timestamp'])
        
        if gaze_around_interaction:
            distance = self.calculate_gaze_interaction_distance(gaze_around_interaction, recent_interaction)
            
            if distance > 200:  # Pixels threshold
                self.suspicion_score += 0.3
                await self.record_suspicion('gaze_interaction_mismatch', {
                    'distance': distance,
                    'interaction_type': recent_interaction['type']
                })
                
    async def analyze_gaze_interaction_correlation(self):
        """Check if gaze naturally precedes interactions"""
        if len(self.interaction_history) < 2 or len(self.gaze_history) < 10:
            return
            
        correlation_scores = []
        
        for i in range(1, len(self.interaction_history)):
            interaction = self.interaction_history[i]
            prev_gaze = self.get_gaze_before_interaction(interaction['timestamp'])
            
            if prev_gaze:
                avg_gaze = self.calculate_average_position(prev_gaze)
                distance = self.calculate_distance(
                    avg_gaze['x'], avg_gaze['y'],
                    interaction['screen_x'], interaction['screen_y']
                )
                correlation_scores.append(distance)
                
        if correlation_scores:
            avg_correlation = sum(correlation_scores) / len(correlation_scores)
            
            if avg_correlation > 150:  # High distance = poor correlation
                self.suspicion_score += 0.4
                await self.record_suspicion('poor_gaze_interaction_correlation', {
                    'average_distance': avg_correlation,
                    'sample_size': len(correlation_scores)
                })
                
    def get_gaze_around_time(self, timestamp):
        """Get gaze data around specific time"""
        return [g for g in self.gaze_history 
                if abs(g['timestamp'] - timestamp) < 500]  # 500ms window
        
    def get_gaze_before_interaction(self, interaction_time):
        """Get gaze data before interaction"""
        return [g for g in self.gaze_history 
                if interaction_time - 500 <= g['timestamp'] <= interaction_time]
                
    def calculate_average_position(self, gaze_data):
        """Calculate average position from gaze data"""
        if not gaze_data:
            return {'x': 0, 'y': 0}
            
        avg_x = sum(g['x'] for g in gaze_data) / len(gaze_data)
        avg_y = sum(g['y'] for g in gaze_data) / len(gaze_data)
        return {'x': avg_x, 'y': avg_y}
        
    def calculate_distance(self, x1, y1, x2, y2):
        """Calculate distance between two points"""
        return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        
    def calculate_gaze_interaction_distance(self, gaze_data, interaction):
        """Calculate distance between gaze and interaction point"""
        if not gaze_data:
            return 0
            
        avg_gaze = self.calculate_average_position(gaze_data)
        return self.calculate_distance(
            avg_gaze['x'], avg_gaze['y'],
            interaction['screen_x'], interaction['screen_y']
        )
        
    async def record_suspicion(self, pattern_type, details):
        """Record suspicious pattern as a violation"""
        violation_data = {
            "id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "type": "remote_control_suspicion",
            "timestamp": datetime.now().isoformat(),
            "details": {
                "pattern": pattern_type,
                **details,
                "overall_suspicion_score": self.suspicion_score
            }
        }
        
        await insert_violation(violation_data)
        await self.update_violation_count()
        
    async def update_violation_count(self):
        """Update the total violation count in the session"""
        # Get current violation count from database
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM violations WHERE session_id = ?",
                (self.session_id,)
            )
            violation_count = (await cursor.fetchone())[0]
            
            # Update session
            await update_session(self.session_id, {
                "total_violations": violation_count
            })
            
            # Check if we reached the limit
            max_violations = 5  # Default
            if self.session_id in sessions and sessions[self.session_id].get("exam_settings"):
                max_violations = sessions[self.session_id]["exam_settings"].get("max_violations", 5)
            
            if violation_count >= max_violations:
                await update_exam_end(self.session_id, "terminated", f"Exceeded maximum violations ({max_violations})")


class NonKeyboardInputMonitor:
    """STRICT monitor for non-keyboard input with exam termination on second violation"""
    
    def __init__(self, session_id):
        self.session_id = session_id
        self.non_keyboard_events = []
        self.violation_count = 0
        self.last_keyboard_time = 0
        self.keyboard_activity = False
        self.exam_terminated = False
        
    async def track_keyboard_activity(self):
        """Track when keyboard is used"""
        self.keyboard_activity = True
        self.last_keyboard_time = time.time() * 1000
        # Reset after short delay
        await asyncio.sleep(30)
        self.keyboard_activity = False
        
    async def track_input_event(self, input_type, data, input_method):
        """Track any input event - DETECTS EVEN SINGLE CHARACTERS"""
        if self.exam_terminated:
            return
            
        current_time = time.time() * 1000
        data_length = len(data) if data else 0
        
        # Check if this is non-keyboard input (ANY length)
        if (input_type != 'keyboard' and data_length > 0 and 
            current_time - self.last_keyboard_time > 1000):  # No keyboard activity for 1 second
            
            event = {
                'type': input_type,
                'data': data,
                'data_length': data_length,
                'method': input_method,
                'timestamp': current_time,
                'keyboard_recently_used': self.keyboard_activity or 
                                        (current_time - self.last_keyboard_time < 30000)
            }
            
            self.non_keyboard_events.append(event)
            await self.analyze_non_keyboard_input(event)
            
    async def analyze_non_keyboard_input(self, event):
        """Analyze non-keyboard input for cheating patterns"""
        if self.exam_terminated:
            return
            
        # ANY non-keyboard input is suspicious
        suspicious_patterns = []
        
        # Pattern 1: Any input without recent keyboard usage
        if not event['keyboard_recently_used']:
            suspicious_patterns.append('input_without_keyboard')
            
        # Pattern 2: Even single character input without keyboard
        if not event['keyboard_recently_used'] and event['data_length'] > 0:
            suspicious_patterns.append('any_input_without_keyboard')
            
        if suspicious_patterns:
            self.violation_count += 1
            
            # Record as a regular violation that counts toward the total
            violation_data = {
                "id": str(uuid.uuid4()),
                "session_id": self.session_id,
                "type": "non_keyboard_input",
                "timestamp": datetime.now().isoformat(),
                "details": {
                    "patterns": suspicious_patterns,
                    "violation_count": self.violation_count,
                    "event_type": event['type'],
                    "data_preview": str(event['data'])[:50] if event['data'] else "",
                    "data_length": event['data_length'],
                    "keyboard_recently_used": event['keyboard_recently_used'],
                    "message": f"Non-keyboard input detected ({self.violation_count} violation(s)) - EXAM WILL BE TERMINATED ON NEXT VIOLATION" if self.violation_count == 1 
                              else f"Non-keyboard input detected ({self.violation_count} violation(s)) - EXAM TERMINATED"
                }
            }
            
            # Insert into violations table
            await insert_violation(violation_data)
            
            # Update session violation count
            await update_session(self.session_id, {
                "total_violations": (sessions.get(self.session_id, {}).get("total_violations", 0) or 0) + 1
            })
            
            # TERMINATE EXAM ON SECOND REMOTE CONTROL VIOLATION
            if self.violation_count >= 2:
                await self.terminate_exam()
                
            # Also check if total violations reached 5 (including this one)
            await self.check_total_violations()
    



    async def check_and_terminate_exam(session_id: str):
        async with aiosqlite.connect(DATABASE_NAME) as db:
            # Get current violation count
            cursor = await db.execute(
                "SELECT total_violations, submission_status FROM sessions WHERE id = ?",
                (session_id,)
            )
            result = await cursor.fetchone()
            
            if not result:
                return
                
            total_violations, submission_status = result
            
            # If exam is already ended, do nothing
            if submission_status in ["submitted", "terminated"]:
                return
            
            # Check for termination conditions
            max_violations = 5  # Default
            if session_id in sessions and sessions[session_id].get("exam_settings"):
                max_violations = sessions[session_id]["exam_settings"].get("max_violations", 5)
            
            if total_violations >= max_violations:
                await update_exam_end(session_id, "terminated", f"Exceeded maximum violations ({max_violations})")

    async def check_total_violations(self):
        """
        Check if total violations reached the exam-specific limit
        pulled from session.exam_settings.max_violations.
        """
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT total_violations FROM sessions WHERE id = ?",
                (self.session_id,)
            )
            result = await cursor.fetchone()
            total_violations = result[0] if result else 0

        # 👇 NEW: read max_violations from the session's exam_settings
        max_violations = 5  # fallback
        if self.session_id in sessions:
            settings = sessions[self.session_id].get("exam_settings", {})
            max_violations = settings.get("max_violations", 5)

        if total_violations >= max_violations:
            await update_exam_end(
                self.session_id,
                "terminated",
                f"Exceeded maximum violations ({max_violations})"
            )

        
    async def terminate_exam(self):
        """Terminate exam due to repeated remote control violations"""
        if self.exam_terminated:
            return
            
        self.exam_terminated = True
        
        # Record termination violation
        termination_data = {
            "id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "type": "exam_termination",
            "timestamp": datetime.now().isoformat(),
            "details": {
                "reason": "Repeated non-keyboard input violations",
                "violation_count": self.violation_count,
                "message": "Exam terminated due to suspected remote control cheating - Two non-keyboard input violations detected"
            }
        }
        
        await insert_violation(termination_data)
        
        # Update session to terminated status
        await update_exam_end(self.session_id, "terminated", "Repeated non-keyboard input violations")


class EnhancedRemoteControlDetector:
    """Enhanced detector that combines multiple detection methods"""
    
    def __init__(self, session_id):
        self.session_id = session_id
        self.mouse_movement_detector = MouseMovementDetector(session_id)
        self.input_lag_detector = RemoteControlDetector(session_id)  # From previous code
        self.detection_started = False
        
    async def start_detection(self):
        """Start all detection methods"""
        self.detection_started = True
        await self.mouse_movement_detector.start_detection()
        await self.input_lag_detector.start_detection()
        print(f"Enhanced remote control detection started for session {self.session_id}")
    
    async def track_actual_mouse_input(self, mouse_event):
        """Track actual mouse input from local system"""
        await self.mouse_movement_detector.track_actual_mouse_input(mouse_event)
        await self.input_lag_detector.track_mouse_input(mouse_event)
    
    async def analyze_screenshot(self, screenshot_data):
        """Analyze screenshot for cursor movement detection"""
        cursor_position = await self.mouse_movement_detector.screenshot_analyzer.analyze_screenshot(screenshot_data)
        
        if cursor_position:
            await self.mouse_movement_detector.track_cursor_position(cursor_position)
        
        return cursor_position





class RemoteControlDetector:
    """Main class to coordinate all detection methods"""
    
    def __init__(self, session_id):
        self.session_id = session_id
        self.timing_detector = ResponseTimingDetector(session_id)
        self.input_detector = InputPatternDetector(session_id)
        self.focus_detector = FocusAnomalyDetector(session_id)
        self.non_keyboard_monitor = NonKeyboardInputMonitor(session_id)
        self.overall_suspicion = 0
        self.detection_started = False
        
    async def start_detection(self):
        """Start remote control detection"""
        self.detection_started = True
        print(f"Remote control detection started for session {self.session_id}")
        
    async def track_user_interaction(self, event_type, **kwargs):
        """Track various user interactions"""
        if not self.detection_started:
            return
            
        # Route to appropriate detectors
        if event_type == 'typing_start':
            await self.timing_detector.track_interaction_start('typing', kwargs.get('element_id'))
        elif event_type == 'typing_end':
            await self.timing_detector.track_interaction_end('typing')
        elif event_type == 'keystroke':
            await self.input_detector.track_keystroke(kwargs['key'], kwargs['timing'], kwargs.get('correct', True))
            await self.non_keyboard_monitor.track_keyboard_activity()
        elif event_type == 'mouse_move':
            await self.input_detector.track_mouse_movement(
                kwargs['x'], kwargs['y'], kwargs.get('speed', 0),
                kwargs.get('acceleration', 0), kwargs.get('event_type', 'move')
            )
        elif event_type == 'click':
            await self.timing_detector.track_interaction_start('click', kwargs.get('element_id'))
            await self.focus_detector.track_interaction('click', kwargs['x'], kwargs['y'])
        elif event_type == 'gaze_position':
            await self.focus_detector.track_gaze_position(kwargs['x'], kwargs['y'], kwargs.get('confidence', 0.5))
        elif event_type == 'non_keyboard_input':
            await self.non_keyboard_monitor.track_input_event(
                kwargs.get('input_type', 'unknown'),
                kwargs.get('data', ''),
                kwargs.get('input_method', 'unknown')
            )
        elif event_type == 'screen_change':
            await self.focus_detector.track_interaction('screen_change', 0, 0)
            
    async def evaluate_suspicion(self):
        """Evaluate overall suspicion level"""
        # Weighted combination of individual suspicion scores
        timing_score = self.timing_detector.suspicion_score * 0.3
        input_score = self.input_detector.suspicion_score * 0.3
        focus_score = self.focus_detector.suspicion_score * 0.2
        non_keyboard_score = self.non_keyboard_monitor.violation_count * 0.2
        
        self.overall_suspicion = timing_score + input_score + focus_score + non_keyboard_score
        
        if self.overall_suspicion > 0.7:
            await self.record_high_suspicion()
            
        return self.overall_suspicion
        
    async def record_high_suspicion(self):
        """Record high suspicion of remote control as a violation"""
        violation_data = {
            "id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "type": "high_remote_control_suspicion",
            "timestamp": datetime.now().isoformat(),
            "details": {
                "overall_suspicion_score": self.overall_suspicion,
                "timing_score": self.timing_detector.suspicion_score,
                "input_score": self.input_detector.suspicion_score,
                "focus_score": self.focus_detector.suspicion_score,
                "non_keyboard_violations": self.non_keyboard_monitor.violation_count,
                "message": "High probability of remote control detected"
            }
        }
        
        await insert_violation(violation_data)
        

            
        # Also update total violation count
        await self.update_violation_count()
    
    async def terminate_for_remote_control(self):
        """Terminate exam for repeated remote control violations"""
        termination_data = {
            "id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "type": "exam_termination",
            "timestamp": datetime.now().isoformat(),
            "details": {
                "reason": "Repeated remote control violations",
                "remote_control_violations": self.remote_control_violations,
                "message": "Exam terminated due to suspected remote control activity"
            }
        }
        
        await insert_violation(termination_data)
        await update_exam_end(self.session_id, "terminated", "Repeated remote control violations")
    
    async def update_violation_count(self):
        """Update the total violation count in the session"""
        # Get current violation count from database
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM violations WHERE session_id = ?",
                (self.session_id,)
            )
            violation_count = (await cursor.fetchone())[0]
            
            # Update session
            await update_session(self.session_id, {
                "total_violations": violation_count
            })
            
            # Check if we reached the limit
            max_violations = 5  # Default
            if self.session_id in sessions and sessions[self.session_id].get("exam_settings"):
                max_violations = sessions[self.session_id]["exam_settings"].get("max_violations", 5)
            
            if violation_count >= max_violations:
                await update_exam_end(self.session_id, "terminated", f"Exceeded maximum violations ({max_violations})")



@app.post("/api/sessions/{session_id}/violations")
async def record_violation(session_id: str, violation: Violation):
    # Check if session exists in database
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")
    
    violation_data = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "type": violation.type,
        "timestamp": datetime.now().isoformat(),
        "details": violation.details,
    }
    
    # Insert into database
    await insert_violation(violation_data)
    
    # Calculate metrics directly from database
    await calculate_session_metrics(session_id)
    
    # Get updated violation count from database
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT total_violations FROM sessions WHERE id = ?",
            (session_id,)
        )
        result = await cursor.fetchone()
        total_violations = result[0] if result else 0
        
        # Get exam-specific max violations if available
        max_violations = 5  # Default
        if session_id in sessions and sessions[session_id].get("exam_settings"):
            max_violations = sessions[session_id]["exam_settings"].get("max_violations", 5)
        
        # Get remote control count for termination logic (but don't return it)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM violations WHERE session_id = ? AND type IN ('non_keyboard_input', 'high_remote_control_suspicion', 'remote_control_suspicion')",
            (session_id,)
        )
        remote_control_count = (await cursor.fetchone())[0]
    
    # Check for automatic termination conditions
    if total_violations >= 5:
        await update_exam_end(session_id, "terminated", f"Exceeded maximum violations ({max_violations})")
    elif remote_control_count >= 2:
        await update_exam_end(session_id, "terminated", "Repeated remote control violations detected")
    
    return {
        "status": "violation_recorded",
        "violation": violation_data,
        "total_violations": total_violations,
        "max_violations": max_violations
    }





@app.get("/", response_class=HTMLResponse)
async def get_root():
    return HTMLResponse(content=HTML_TEMPLATE)


@app.get("/api/sessions/{session_id}/monitor-check")
async def check_monitors(session_id: str, monitor_count: int = 1):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        # Update monitor status
        sessions[session_id]["behavior_monitoring_status"] = "active"
        await update_session(session_id, {
            "behavior_monitoring_status": "active"
        })
        
        # Record violation if more than one monitor
        if monitor_count > 1:
            violation_data = {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "type": "multiple_monitors",
                "timestamp": datetime.now().isoformat(),
                "details": {"monitor_count": monitor_count},
            }
            
            sessions[session_id]["violations"].append(violation_data)
            
            # Insert into database
            await insert_violation(violation_data)
            
            # DYNAMIC UPDATE: Recalculate metrics after adding violation
            await calculate_session_metrics(session_id)
            
            return {
                "status": "violation_detected",
                "monitor_count": monitor_count,
                "violation": violation_data
            }
        else:
            return {
                "status": "acceptable",
                "monitor_count": monitor_count
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Monitor detection failed: {str(e)}")



@app.get("/api/sessions/{session_id}/check-remote-tools")
async def check_remote_tools(session_id: str):
    """Check for remote control software indicators"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")
    
    # Simulate detection (in real implementation, this would analyze behavioral patterns)
    detection_data = {
        "timestamp": datetime.now().isoformat(),
        "checks_performed": ["behavioral_analysis", "network_patterns", "input_consistency"],
        "detected_indicators": [],
        "confidence": "low"
    }
    
    # 10% chance of detection for demo
    if random.random() < 0.1:
        indicators = ["unnatural_mouse_patterns", "consistent_typing_timing", "rapid_focus_changes"]
        detected_indicator = random.choice(indicators)
        detection_data["detected_indicators"].append(detected_indicator)
        detection_data["confidence"] = "medium"
        
        await record_violation(session_id, Violation(
            type="remote_control_indicators",
            details={
                "indicator": detected_indicator,
                "confidence": "medium",
                "timestamp": datetime.now().isoformat()
            }
        ))
    
    return detection_data


@app.post("/api/sessions/{session_id}/monitor-analysis")
async def monitor_analysis(session_id: str, monitor_data: dict):
    """Advanced multi-monitor detection"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    detection_methods = []
    monitor_count = 1
    
    # Method 1: Screen Details API (most reliable)
    if monitor_data.get('screen_details'):
        monitor_count = monitor_data['screen_details']['count']
        detection_methods.append('screen_details_api')
    
    # Method 2: Window positioning analysis
    if monitor_count == 1 and monitor_data.get('window_position'):
        if analyze_window_position(monitor_data['window_position']):
            monitor_count = 2
            detection_methods.append('window_position_analysis')
    
    # Method 3: Display resolution analysis
    if monitor_count == 1 and monitor_data.get('display_info'):
        if analyze_display_resolution(monitor_data['display_info']):
            monitor_count = 2
            detection_methods.append('resolution_analysis')
    
    # Record violation if multiple monitors detected
    if monitor_count > 1:
        violation_details = {
            "monitor_count": monitor_count,
            "detection_methods": detection_methods,
            "confidence": "high" if 'screen_details_api' in detection_methods else "medium",
            "timestamp": datetime.now().isoformat()
        }
        
        await record_violation(session_id, Violation(
            type="multiple_monitors",
            details=violation_details
        ))
        
        return {
            "status": "violation",
            "message": f"Multiple monitors detected ({monitor_count})",
            "details": violation_details
        }
    
    return {"status": "acceptable", "monitor_count": monitor_count}

def analyze_window_position(position_data):
    """Analyze window position for multi-monitor indicators"""
    # Window positioned off-screen (common with multiple monitors)
    if (position_data['x'] + position_data['width'] < 0 or 
        position_data['y'] + position_data['height'] < 0):
        return True
    
    # Window spanning unusually wide area
    if position_data['width'] > position_data['screen_width'] * 1.2:
        return True
    
    return False

def analyze_display_resolution(display_info):
    """Analyze display resolution for multi-monitor indicators"""
    # Significant difference between screen size and available size
    if (display_info['avail_width'] > display_info['screen_width'] * 1.1 or
        display_info['avail_left'] != 0 or
        display_info['avail_top'] != 0):
        return True
    
    return False
    
    
@app.get("/api/available-exams")
async def get_available_exams():
    """Get list of active exams available to students"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT id, name, duration, description FROM exams WHERE is_active = 1 ORDER BY name"
        )
        rows = await cursor.fetchall()
        
        exams = []
        for row in rows:
            exams.append({
                "id": row[0],
                "name": row[1],
                "duration": row[2],
                "description": row[3]
            })
        
        return {"exams": exams}
    


async def take_screenshots(session_id: str, interval: int = 5, quality: int = 70):
    """Continuously take screenshots at specified intervals"""
    with mss.mss() as sct:
        # Get all monitors
        monitors = sct.monitors
        
        # Remove the session existence check from the while condition
        # Use a separate flag to control the loop
        should_continue = True
        
        while should_continue and session_id in screenshot_tasks:
            try:
                # Capture all monitors
                screenshot = sct.grab(monitors[0])  # Primary monitor
                
                # Convert to PIL Image
                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                
                # Convert to JPEG with specified quality
                img_buffer = io.BytesIO()
                img.save(img_buffer, format="JPEG", quality=quality)
                img_data = img_buffer.getvalue()
                
                # Store screenshot data directly in database
                screenshot_data = {
                    "id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                    "data": img_data,
                    "format": "jpeg"
                }
                
                # Insert into database
                await insert_screenshot(screenshot_data)
                
                # Send to mobile if connected
                if session_id in mobile_connections:
                    try:
                        await mobile_connections[session_id].send_json({
                            "type": "screenshot",
                            "timestamp": screenshot_data["timestamp"],
                            "data": base64.b64encode(img_data).decode('utf-8'),
                            "format": "jpeg"
                        })
                    except Exception as e:
                        print(f"Failed to send screenshot to mobile: {e}")
                
                # Wait for the next interval
                await asyncio.sleep(interval)
                
            except Exception as e:
                print(f"Screenshot error: {e}")
                should_continue = False  # Stop on error
                await asyncio.sleep(1)


@app.post("/api/sessions/{session_id}/start-screenshots")
async def start_screenshots(session_id: str, config: ScreenshotConfig):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Stop existing screenshot task if any
    if session_id in screenshot_tasks:
        screenshot_tasks[session_id].cancel()
    
    # Start new screenshot task
    screenshot_tasks[session_id] = asyncio.create_task(
        take_screenshots(session_id, config.interval, config.quality)
    )
    
    # Update status
    sessions[session_id]["face_detection_status"] = "active"
    await update_session(session_id, {
        "face_detection_status": "active"
    })
    
    return {
        "status": "screenshots_started",
        "interval": config.interval,
        "quality": config.quality
    }


@app.post("/api/sessions/{session_id}/stop-screenshots")
async def stop_screenshots(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session_id in screenshot_tasks:
        screenshot_tasks[session_id].cancel()
        del screenshot_tasks[session_id]
        
        # Update status - BOTH in memory and database
        sessions[session_id]["face_detection_status"] = "inactive"
        await update_session(session_id, {
            "face_detection_status": "inactive"
        })
        
        return {"status": "screenshots_stopped"}
    
    return {"status": "no_screenshot_task"}

@app.post("/api/sessions/{session_id}/stop-recording")
async def stop_recording(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session_id in recording_tasks:
        if session_id in recording_state:
            recording_state[session_id]["recording"] = False
        
        # Wait a bit for the task to finish
        await asyncio.sleep(0.5)
        
        if session_id in recording_tasks:
            recording_tasks[session_id].cancel()
            del recording_tasks[session_id]
        
        # Update status - BOTH in memory and database
        sessions[session_id]["behavior_monitoring_status"] = "inactive"
        await update_session(session_id, {
            "behavior_monitoring_status": "inactive"
        })
        
        return {"status": "recording_stopped"}
    
    return {"status": "no_recording_task"}



@app.post("/api/sessions/{session_id}/start-audio-monitoring")
async def start_audio_monitoring(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    await start_audio_monitoring_internal(session_id)
    
    return {"status": "audio_monitoring_started"}

@app.post("/api/sessions/{session_id}/stop-audio-monitoring")
async def stop_audio_monitoring(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Update status
    sessions[session_id]["audio_monitoring_status"] = "inactive"
    await update_session(session_id, {
        "audio_monitoring_status": "inactive"
    })
    
    return {"status": "audio_monitoring_stopped"}

async def record_screen(session_id: str, fps: int = 10, quality: int = 50):
    """Record screen from session creation to session end - FIXED VERSION"""
    print(f"🎥 Starting recording for session {session_id}")
    
    with mss.mss() as sct:
        # Get primary monitor
        monitor = sct.monitors[0]
        
        # Create a unique video file for this recording session
        video_filename = f"session_{session_id}_{int(time.time())}.mp4"
        video_path = os.path.join(VIDEO_FOLDER, video_filename)
        
        # ✅ FIX: Initialize recording state PROPERLY with recording=True
        recording_state[session_id] = {
            "fps": fps,
            "quality": quality,
            "video_path": video_path,
            "start_time": datetime.now().isoformat(),
            "recording": True,  # ✅ CRITICAL: Set this to True immediately
            "frame_count": 0,
            "video_writer": None,
            "session_active": True
        }
        
        frame_interval = 1.0 / fps
        
        try:
            # Get initial frame to determine dimensions
            screenshot = sct.grab(monitor)
            frame = np.array(screenshot)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            height, width = frame.shape[:2]
            
            # Initialize video writer
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            video_writer = cv2.VideoWriter(video_path, fourcc, fps, (width, height))
            recording_state[session_id]["video_writer"] = video_writer
            
            print(f"🎬 Recording started for session {session_id} at {video_path}")
            
            # ✅ FIX: Use a more reliable while condition
            while (session_id in recording_state and 
                   recording_state[session_id].get("recording", False) and
                   recording_state[session_id].get("session_active", True)):
                
                start_time = time.time()
                
                try:
                    # Capture screen
                    screenshot = sct.grab(monitor)
                    frame = np.array(screenshot)
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    
                    # Write frame to video
                    video_writer.write(frame)
                    recording_state[session_id]["frame_count"] += 1
                    
                    # Calculate time to wait for next frame
                    processing_time = time.time() - start_time
                    wait_time = max(0, frame_interval - processing_time)
                    await asyncio.sleep(wait_time)
                    
                except Exception as e:
                    print(f"❌ Frame capture error: {e}")
                    await asyncio.sleep(0.1)  # Brief pause on error
                    
        except Exception as e:
            print(f"❌ Recording setup error: {e}")
        finally:
            # Finalize recording when loop ends
            await finalize_recording(session_id)




async def finalize_recording(session_id: str):
    """Finalize the recording and save to database"""
    if session_id not in recording_state:
        return
        
    try:
        # Close video writer
        if recording_state[session_id]["video_writer"]:
            recording_state[session_id]["video_writer"].release()
        
        # Get file info
        video_path = recording_state[session_id]["video_path"]
        file_size = 0
        if os.path.exists(video_path):
            file_size = os.path.getsize(video_path)
        
        # Save recording in database
        recording_data = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "start_time": recording_state[session_id]["start_time"],
            "end_time": datetime.now().isoformat(),
            "fps": recording_state[session_id]["fps"],
            "quality": recording_state[session_id]["quality"],
            "frame_count": recording_state[session_id]["frame_count"],
            "video_path": video_path,
            "video_filename": os.path.basename(video_path),
            "file_size": file_size
        }
        
        await insert_recording(recording_data)
        print(f"✅ Recording finalized for session {session_id}. Frames: {recording_state[session_id]['frame_count']}")
        
    except Exception as e:
        print(f"❌ Error finalizing recording: {e}")
    finally:
        # Clean up
        if session_id in recording_state:
            del recording_state[session_id]



async def start_recording_immediately(session_id: str, config: RecordingConfig):
    """Start recording immediately without waiting for exam start"""
    # Stop existing recording task if any
    if session_id in recording_tasks:
        recording_tasks[session_id].cancel()
        if session_id in recording_state:
            recording_state[session_id]["recording"] = False
    
    # ✅ FIX: Ensure recording state exists before starting
    if session_id not in recording_state:
        recording_state[session_id] = {}
    
    # Start new recording task
    recording_tasks[session_id] = asyncio.create_task(
        record_screen(session_id, config.fps, config.quality)
    )
    
    # Update database
    await update_session(session_id, {
        "behavior_monitoring_status": "active"
    })
    
    # Also update in-memory session
    if session_id in sessions:
        sessions[session_id]["behavior_monitoring_status"] = "active"
    
    print(f"✅ Recording task started immediately for session {session_id}")


    

@app.post("/api/sessions/{session_id}/submit-exam")
async def submit_exam(session_id: str):
    """Handle exam submission and stop recording"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        # Update exam end
        await update_exam_end(session_id, "submitted", "Exam submitted successfully")
        
        # Stop recording
        await stop_recording(session_id)
        
        return {
            "status": "exam_submitted",
            "message": "Exam submitted successfully",
            "submission_time": datetime.now().isoformat()
        }
        
    except Exception as e:
        print(f"❌ Exam submission error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to submit exam: {str(e)}")



async def stop_all_monitoring(session_id: str):
    """Stop all monitoring activities for a session"""
    # Stop screenshots
    if session_id in screenshot_tasks:
        screenshot_tasks[session_id].cancel()
        del screenshot_tasks[session_id]
    
    # Stop recording
    if session_id in recording_tasks:
        if session_id in recording_state:
            recording_state[session_id]["recording"] = False
        await asyncio.sleep(0.5)
        if session_id in recording_tasks:
            recording_tasks[session_id].cancel()
            del recording_tasks[session_id]
    
    # Update all statuses to inactive
    await update_session(session_id, {
        "face_detection_status": "inactive",
        "audio_monitoring_status": "inactive", 
        "behavior_monitoring_status": "inactive",
        "ended_at": datetime.now().isoformat(),
        "submission_status": "submitted"
    })


@app.post("/api/sessions/{session_id}/start-recording")
async def start_recording(session_id: str, config: RecordingConfig):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Stop existing recording task if any
    if session_id in recording_tasks:
        recording_tasks[session_id].cancel()
        if session_id in recording_state:
            recording_state[session_id]["recording"] = False
    
    # Start new recording task
    recording_tasks[session_id] = asyncio.create_task(
        record_screen(session_id, config.fps, config.quality)
    )
    
    # Update status
    sessions[session_id]["behavior_monitoring_status"] = "active"
    await update_session(session_id, {
        "behavior_monitoring_status": "active"
    })
    
    return {
        "status": "recording_started",
        "fps": config.fps,
        "quality": config.quality
    }


@app.post("/api/sessions/{session_id}/stop-recording")
async def stop_recording(session_id: str):
    """Stop recording for a session"""
    if session_id in recording_tasks:
        # Signal recording to stop
        if session_id in recording_state:
            recording_state[session_id]["recording"] = False
        
        # Wait for recording to finish
        await asyncio.sleep(1)
        
        # Cancel the task
        recording_tasks[session_id].cancel()
        del recording_tasks[session_id]
        
        # Update status
        await update_session(session_id, {
            "behavior_monitoring_status": "inactive"
        })
        
        print(f"⏹️ Recording stopped for session {session_id}")



@app.get("/api/sessions/{session_id}/recording-status")
async def get_recording_status(session_id: str):
    """Get the current recording status for a session"""
    recording_active = session_id in recording_tasks and session_id in recording_state
    
    if recording_active:
        return {
            "active": True,
            "fps": recording_state[session_id].get("fps", 0),
            "frame_count": recording_state[session_id].get("frame_count", 0),
            "start_time": recording_state[session_id].get("start_time"),
            "video_path": recording_state[session_id].get("video_path")
        }
    else:
        # Check if recording exists in database
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT start_time, end_time, frame_count, video_path FROM recordings WHERE session_id = ? ORDER BY start_time DESC LIMIT 1",
                (session_id,)
            )
            recording = await cursor.fetchone()
            
            if recording:
                return {
                    "active": False,
                    "start_time": recording[0],
                    "end_time": recording[1],
                    "frame_count": recording[2],
                    "video_path": recording[3],
                    "completed": True
                }
            else:
                return {"active": False, "exists": False}

@app.get("/api/sessions/{session_id}/recordings")
async def get_recordings(session_id: str):
    """Get all recordings for a session"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT id, start_time, end_time, fps, quality, frame_count, video_path, video_filename, file_size FROM recordings WHERE session_id = ? ORDER BY start_time DESC",
            (session_id,)
        )
        rows = await cursor.fetchall()
        
        recordings = []
        for row in rows:
            video_path = row[6]
            file_exists = os.path.exists(video_path) if video_path else False
            
            recordings.append({
                "id": row[0],
                "start_time": row[1],
                "end_time": row[2],
                "fps": row[3],
                "quality": row[4],
                "frame_count": row[5],
                "video_path": video_path,
                "video_filename": row[7],
                "file_size": row[8],
                "file_exists": file_exists,
                "play_url": f"/api/recordings/play/{row[0]}" if file_exists else None,
                "download_url": f"/api/recordings/download/{row[0]}" if file_exists else None
            })
    
    return {
        "count": len(recordings),
        "recordings": recordings
    }



@app.get("/api/recordings/download/{recording_id}")
async def download_recording(recording_id: str):
    """Download a recording video file from database"""
    # Get recording from database
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT video_data, video_filename FROM recordings WHERE id = ?",
            (recording_id,)
        )
        row = await cursor.fetchone()
        
        if row and row[0]:
            return Response(
                content=row[0],  # The BLOB data
                media_type="video/mp4",
                headers={
                    "Content-Disposition": f"attachment; filename={row[1] or f'recording_{recording_id}.mp4'}"
                }
            )
    
    raise HTTPException(status_code=404, detail="Recording not found")



@app.get("/api/recordings/play/{recording_id}")
async def play_recording(recording_id: str):
    """Stream a recording video file"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT video_path, video_filename FROM recordings WHERE id = ?",
            (recording_id,)
        )
        row = await cursor.fetchone()
        
        if row and row[0] and os.path.exists(row[0]):
            return FileResponse(
                row[0],
                media_type="video/mp4",
                filename=row[1] or f"recording_{recording_id}.mp4"
            )
    
    raise HTTPException(status_code=404, detail="Recording not found")

@app.get("/api/recordings/download/{recording_id}")
async def download_recording(recording_id: str):
    """Download a recording video file"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT video_path, video_filename FROM recordings WHERE id = ?",
            (recording_id,)
        )
        row = await cursor.fetchone()
        
        if row and row[0] and os.path.exists(row[0]):
            return FileResponse(
                row[0],
                media_type="video/mp4",
                filename=row[1] or f"recording_{recording_id}.mp4",
                headers={"Content-Disposition": f"attachment; filename={row[1] or f'recording_{recording_id}.mp4'}"}
            )
    
    raise HTTPException(status_code=404, detail="Recording not found")




@app.get("/api/recordings/{recording_id}/info")
async def get_recording_info(recording_id: str):
    """Get recording metadata without the video data"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT id, session_id, start_time, end_time, fps, quality, frame_count, video_filename, file_size FROM recordings WHERE id = ?",
            (recording_id,)
        )
        row = await cursor.fetchone()
        
        if row:
            return {
                "id": row[0],
                "session_id": row[1],
                "start_time": row[2],
                "end_time": row[3],
                "fps": row[4],
                "quality": row[5],
                "frame_count": row[6],
                "video_filename": row[7],
                "file_size": row[8],
                "download_url": f"/api/recordings/download/{row[0]}",
                "play_url": f"/api/recordings/play/{row[0]}"
            }
    
    raise HTTPException(status_code=404, detail="Recording not found")


@app.get("/api/admin/recordings")
async def admin_list_recordings():
    """Admin endpoint to list all recordings with file status"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT id, session_id, start_time, end_time, fps, frame_count, video_path, video_filename, file_size FROM recordings ORDER BY start_time DESC"
        )
        rows = await cursor.fetchall()
        
        recordings = []
        for row in rows:
            video_path = row[6]
            file_exists = os.path.exists(video_path) if video_path else False
            file_size = os.path.getsize(video_path) if file_exists else 0
            
            recordings.append({
                "id": row[0],
                "session_id": row[1],
                "start_time": row[2],
                "end_time": row[3],
                "fps": row[4],
                "frame_count": row[5],
                "video_path": video_path,
                "video_filename": row[7],
                "file_size": file_size,
                "file_exists": file_exists,
                "file_size_mb": f"{(file_size / 1024 / 1024):.2f} MB" if file_exists else "0 MB",
                "play_url": f"http://localhost:8000/api/recordings/play/{row[0]}" if file_exists else None,
                "download_url": f"http://localhost:8000/api/recordings/download/{row[0]}" if file_exists else None
            })
    
    return {
        "recordings": recordings,
        "video_folder": os.path.abspath(VIDEO_FOLDER)
    }


@app.get("/api/debug/session/{session_id}")
async def debug_session(session_id: str):
    """Debug endpoint to check session status"""
    # Check in-memory session but exclude binary data
    in_memory_session = sessions.get(session_id, {})
    
    # Create a safe copy without binary data
    in_memory_safe = {}
    for key, value in in_memory_session.items():
        if isinstance(value, (str, int, float, bool, type(None))):
            in_memory_safe[key] = value
        elif isinstance(value, list):
            # Handle lists - convert items but skip binary data
            safe_list = []
            for item in value:
                if isinstance(item, dict):
                    safe_item = {}
                    for k, v in item.items():
                        if isinstance(v, (str, int, float, bool, type(None))):
                            safe_item[k] = v
                        else:
                            safe_item[k] = f"[{type(v).__name__}]"
                    safe_list.append(safe_item)
                else:
                    safe_list.append(f"[{type(item).__name__}]")
            in_memory_safe[key] = safe_list
        elif isinstance(value, dict):
            # Handle nested dictionaries
            safe_dict = {}
            for k, v in value.items():
                if isinstance(v, (str, int, float, bool, type(None))):
                    safe_dict[k] = v
                else:
                    safe_dict[k] = f"[{type(v).__name__}]"
            in_memory_safe[key] = safe_dict
        else:
            in_memory_safe[key] = f"[{type(value).__name__}]"
    
    # Check database session - EXCLUDE binary data fields
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT id, created_at, student_id, student_name, exam_name, exam_duration, "
            "verification_status, mobile_connected, mobile_status, face_detection_status, "
            "audio_monitoring_status, behavior_monitoring_status, verification_attempts, "
            "started_at, ended_at, submission_status, termination_reason, total_violations, "
            "severity_level, review_status FROM sessions WHERE id = ?", 
            (session_id,)
        )
        columns = [description[0] for description in cursor.description]
        db_row = await cursor.fetchone()
        db_session = dict(zip(columns, db_row)) if db_row else {}
    
    return {
        "in_memory": in_memory_safe,
        "database": db_session,
        "match": in_memory_session.get("verification_status") == db_session.get("verification_status")
    }


@app.get("/api/debug/session/{session_id}/verification")
async def debug_verification(session_id: str):
    """Debug endpoint specifically for verification status"""
    # Check database directly
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Get session verification status
        cursor = await db.execute(
            "SELECT verification_status, verification_attempts FROM sessions WHERE id = ?",
            (session_id,)
        )
        session_result = await cursor.fetchone()
        
        # Check if ID image exists
        cursor = await db.execute(
            "SELECT COUNT(*) FROM id_images WHERE session_id = ?",
            (session_id,)
        )
        id_count = (await cursor.fetchone())[0]
        
        # Check if face image exists
        cursor = await db.execute(
            "SELECT COUNT(*) FROM face_images WHERE session_id = ?",
            (session_id,)
        )
        face_count = (await cursor.fetchone())[0]
    
    return {
        "session_id": session_id,
        "database_verification_status": session_result[0] if session_result else "not_found",
        "database_verification_attempts": session_result[1] if session_result else 0,
        "id_images_count": id_count,
        "face_images_count": face_count,
        "in_memory_verification_status": sessions.get(session_id, {}).get("verification_status", "not_found"),
        "in_memory_verification_attempts": sessions.get(session_id, {}).get("verification_attempts", 0)
    }

@app.get("/api/debug/session/{session_id}/images")
async def debug_images(session_id: str):
    """Debug endpoint to check image data (without returning binary)"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Get ID images metadata
        cursor = await db.execute(
            "SELECT id, upload_time, LENGTH(image_data) as size FROM id_images WHERE session_id = ? ORDER BY upload_time",
            (session_id,)
        )
        id_images = await cursor.fetchall()
        
        # Get face images metadata
        cursor = await db.execute(
            "SELECT id, capture_time, LENGTH(image_data) as size FROM face_images WHERE session_id = ? ORDER BY capture_time",
            (session_id,)
        )
        face_images = await cursor.fetchall()
    
    return {
        "session_id": session_id,
        "id_images": [
            {"id": row[0], "upload_time": row[1], "size_bytes": row[2]} 
            for row in id_images
        ],
        "face_images": [
            {"id": row[0], "capture_time": row[1], "size_bytes": row[2]} 
            for row in face_images
        ]
    }



@app.post("/api/verify-student")
async def verify_student_info(
    student_id: str = Form(...),
    student_name: str = Form(...),
    exam_id: str = Form(None)
):
    """Verify student information against database with STRICT matching"""
    try:
        # Enhanced validation
        if not student_id or not student_name:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Student ID and name are required"}
            )
        
        # Clean and validate inputs
        student_id_clean = student_id.strip()
        student_name_clean = student_name.strip().title()
        
        if not student_id_clean or not student_name_clean:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Invalid student ID or name format"}
            )
        
        # Check if student exists in database
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT id, name, email FROM students WHERE id = ?",
                (student_id_clean,)
            )
            student_row = await cursor.fetchone()
        
        if not student_row:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Student ID not found in system"}
            )
        
        db_student_id, db_student_name, db_student_email = student_row
        
        # STRICT NAME MATCHING - FIXED
        if not exact_name_match(db_student_name, student_name_clean):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error", 
                    "message": "Name does not match student ID records",
                    "details": {
                        "provided_name": student_name_clean,
                        "expected_name": db_student_name
                    }
                }
            )
        
        # Validate exam if provided
        exam_data = {}
        exam_settings = {}
        if exam_id and exam_id.strip():
            exam_id_clean = exam_id.strip()
            async with aiosqlite.connect(DATABASE_NAME) as db:
                cursor = await db.execute(
                    "SELECT id, name, duration, enable_face_detection, enable_audio_monitoring, "
                    "enable_behavior_monitoring, enable_screen_capture, enable_mobile_monitoring, "
                    "max_verification_attempts, max_violations FROM exams WHERE id = ? AND is_active = 1",
                    (exam_id_clean,)
                )
                exam_row = await cursor.fetchone()
                
                if not exam_row:
                    return JSONResponse(
                        status_code=400,
                        content={"status": "error", "message": "Invalid exam ID or exam not active"}
                    )
                
                exam_data = {
                    "exam_id": exam_row[0],
                    "exam_name": exam_row[1],
                    "exam_duration": exam_row[2]
                }
                
                exam_settings = {
                    "enable_face_detection": bool(exam_row[3]),
                    "enable_audio_monitoring": bool(exam_row[4]),
                    "enable_behavior_monitoring": bool(exam_row[5]),
                    "enable_screen_capture": bool(exam_row[6]),
                    "enable_mobile_monitoring": bool(exam_row[7]),
                    "max_verification_attempts": exam_row[8],
                    "max_violations": exam_row[9]
                }
        
        # Create session for verified student
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        # Create session with exam details
        session_data = {
            "id": session_id,
            "created_at": now,
            "violations": [],
            "mobile_connected": False,
            "mobile_status": "disconnected",
            "face_detection_status": "initializing",
            "audio_monitoring_status": "initializing",
            "behavior_monitoring_status": "initializing",
            "screenshots": [],
            "recordings": [],
            "verification_status": "completed",  # Mark as completed since ID verification passed
            "verification_attempts": 0,
            "student_id": db_student_id,
            "student_name": db_student_name,
            "exam_name": exam_data.get("exam_name"),
            "exam_duration": exam_data.get("exam_duration"),
            "exam_id": exam_data.get("exam_id"),
            "started_at": None,
            "ended_at": None,
            "submission_status": "not_started",
            "termination_reason": None,
            "total_violations": 0,
            "severity_level": "low",
            "review_status": "pending",
            "exam_settings": exam_settings
        }
        
        # Store in memory
        sessions[session_id] = session_data
        
        # Insert into database
        await insert_session(session_data)
        
        # Link to exam if exam_id provided
        if exam_data.get("exam_id"):
            await link_session_to_exam(session_id, exam_data["exam_id"])
        
        return {
            "status": "verified",
            "session_id": session_id,
            "student_id": db_student_id,
            "student_name": db_student_name,
            "exam_data": exam_data,
            "message": "Identity verified successfully"
        }
            
    except Exception as e:
        print(f"Verification error: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Verification error: {str(e)}"}
        )

def exact_name_match(db_name: str, provided_name: str) -> bool:
    """
    STRICT name matching to prevent different names from passing
    """
    # Clean names
    db_clean = re.sub(r'\s+', ' ', db_name.strip().lower())
    provided_clean = re.sub(r'\s+', ' ', provided_name.strip().lower())
    
    # Exact match (primary check)
    if db_clean == provided_clean:
        return True
    
    # Split into parts for more sophisticated matching
    db_parts = set(db_clean.split())
    provided_parts = set(provided_clean.split())
    
    # Require at least 80% match and same number of parts ±1
    if abs(len(db_parts) - len(provided_parts)) > 1:
        return False
    
    # Calculate similarity
    intersection = db_parts.intersection(provided_parts)
    similarity = len(intersection) / max(len(db_parts), len(provided_parts))
    
    # Also check if one is substring of another (but not too lenient)
    is_substring = (
        db_clean in provided_clean or 
        provided_clean in db_clean
    )
    
    # Strict conditions: high similarity OR reasonable substring match
    return similarity >= 0.8 or (is_substring and similarity >= 0.6)




@app.post("/api/sessions/{session_id}/upload-id")
async def upload_id_card(session_id: str, file: UploadFile = File(...)):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        # Read the uploaded file
        contents = await file.read()
        
        # Convert to OpenCV image
        nparr = np.frombuffer(contents, np.uint8)
        id_image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if id_image is None:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Invalid image file"}
            )
        
        # Use specialized face detection for ID cards
        has_face, face_count = detect_faces(contents)
        
        if not has_face:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "No face detected in ID image. Please ensure your face is clearly visible."}
            )
        
        # Simple check for image quality (not too dark/blank)
        gray = cv2.cvtColor(id_image, cv2.COLOR_BGR2GRAY)
        if np.mean(gray) < 50:  # Image is too dark
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Image is too dark or blurry"}
            )
        
        # Store ID image in database (store binary data)
        upload_time = datetime.now().isoformat()
        await insert_id_image(session_id, contents, upload_time)
        
        # Update session
        sessions[session_id]["id_image"] = contents
        sessions[session_id]["id_upload_time"] = upload_time
        
        return {
            "status": "id_uploaded",
            "message": "ID card uploaded successfully",
            "face_detected": True,
            "face_count": face_count
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing ID card: {str(e)}")


@app.post("/api/sessions/{session_id}/capture-face")
async def capture_face(session_id: str, file: UploadFile = File(...)):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        print(f"DEBUG: Starting face capture for session {session_id}")
        
        # Read the uploaded file
        contents = await file.read()
        print(f"DEBUG: Received face image of size {len(contents)} bytes")
        
        # Preprocess the image
        processed_contents = preprocess_image(contents)
        
        # Check if image contains at least one face
        has_face, face_count = detect_faces(processed_contents, is_id_card=False)
        print(f"DEBUG: Face detection result - has_face: {has_face}, count: {face_count}")
        
        # Allow multiple faces but require at least one
        if not has_face:
            # Update verification attempts in database
            await update_session(session_id, {
                "verification_attempts": sessions[session_id].get("verification_attempts", 0) + 1
            })
            
            attempts = sessions[session_id].get("verification_attempts", 0)
            print(f"DEBUG: Face detection failed, attempt {attempts}")
            
            if attempts >= 3:
                print("DEBUG: Maximum verification attempts reached")
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "Maximum verification attempts reached. No face detected."}
                )
            
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "No face detected. Please ensure your face is clearly visible and well-lit."}
            )
        
        # Save face image to database FIRST
        capture_time = datetime.now().isoformat()
        await insert_face_image(session_id, processed_contents, capture_time)
        print(f"DEBUG: Face image saved to database for session {session_id}")
        
        # Get ID image from database for comparison
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT image_data FROM id_images WHERE session_id = ? ORDER BY upload_time DESC LIMIT 1",
                (session_id,)
            )
            id_image_row = await cursor.fetchone()
        
        if not id_image_row:
            print("DEBUG: No ID image found for comparison")
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "No ID image found. Please upload ID first."}
            )
        
        id_image_data = id_image_row[0]
        
        # Use enhanced verification with better error handling
        try:
            is_match, confidence, details = enhanced_face_verification(id_image_data, processed_contents, strict_mode=False)  # Non-strict mode
        except Exception as e:
            print(f"ERROR: Verification failed: {e}")
            # Fallback to simple comparison
            is_match = compare_images_simple(id_image_data, processed_contents)
            confidence = 0.5 if is_match else 0.0
            details = {"error": str(e), "fallback_used": True}

        
        print(f"DEBUG: Verification result - match: {is_match}, confidence: {confidence}")
        print(f"DEBUG: Verification details: {details}")
        
        if is_match and confidence >= 0.57:  # Reduced threshold
            # Update session verification status in database
            await update_session(session_id, {
                "verification_status": "completed",
                "verification_attempts": 0
            })
            
            # Also update in-memory session
            sessions[session_id]["verification_status"] = "completed"
            sessions[session_id]["verification_attempts"] = 0
            
            print(f"DEBUG: Face verification successful for session {session_id}")
            
            return {
                "status": "face_captured",
                "message": "Face captured and verified successfully",
                "face_match": True,
                "confidence": round(confidence, 2),
                "verification_attempts": 0,
                "exam_started": True
            }
        else:
            # Update verification attempts
            await update_session(session_id, {
                "verification_attempts": sessions[session_id].get("verification_attempts", 0) + 1
            })
            
            attempts = sessions[session_id].get("verification_attempts", 0)
            print(f"DEBUG: Face mismatch, attempt {attempts}")
            
            # Provide more specific error messages
            error_msg = "Face doesn't match ID photo. Please try again with better lighting and ensure it's really you."
            
            if "image_quality" in details and not details["image_quality"]["face_quality"]:
                error_msg = f"Face image quality issue: {details['image_quality']['face_quality_msg']}"
            elif "image_quality" in details and not details["image_quality"]["id_quality"]:
                error_msg = f"ID image quality issue: {details['image_quality']['id_quality_msg']}"
            elif confidence > 0.4:  # Close match
                error_msg = "Close match but not confident enough. Please try again."
            
            if attempts >= 3:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "Maximum verification attempts reached. " + error_msg}
                )
            
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": error_msg}
            )
    
    except Exception as e:
        print(f"ERROR: Face capture failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing face image: {str(e)}")




@app.get("/api/sessions/{session_id}/verification-db-check")
async def check_verification_db(session_id: str):
    """Debug endpoint to check verification status directly from database"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT verification_status, verification_attempts FROM sessions WHERE id = ?",
            (session_id,)
        )
        result = await cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Session not found in database")
        
        # Check if ID and face images exist
        cursor = await db.execute(
            "SELECT COUNT(*) FROM id_images WHERE session_id = ?",
            (session_id,)
        )
        id_count = (await cursor.fetchone())[0]
        
        cursor = await db.execute(
            "SELECT COUNT(*) FROM face_images WHERE session_id = ?",
            (session_id,)
        )
        face_count = (await cursor.fetchone())[0]
        
        return {
            "database_verification_status": result[0],
            "database_verification_attempts": result[1],
            "id_images_count": id_count,
            "face_images_count": face_count,
            "in_memory_status": sessions.get(session_id, {}).get("verification_status", "not_found")
        }


@app.get("/api/sessions/{session_id}/verification-status")
async def get_verification_status(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Check if ID and face images exist in database
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Check for ID image
        cursor = await db.execute(
            "SELECT COUNT(*) FROM id_images WHERE session_id = ?",
            (session_id,)
        )
        id_count = (await cursor.fetchone())[0]
        
        # Check for face image
        cursor = await db.execute(
            "SELECT COUNT(*) FROM face_images WHERE session_id = ?",
            (session_id,)
        )
        face_count = (await cursor.fetchone())[0]
    
    verification_data = {
        "status": sessions[session_id]["verification_status"],
        "student_id": sessions[session_id].get("student_id"),
        "student_name": sessions[session_id].get("student_name"),
        "id_uploaded": id_count > 0,
        "face_captured": face_count > 0,
        "verification_attempts": sessions[session_id].get("verification_attempts", 0)
    }
    
    return verification_data


@app.post("/api/sessions/{session_id}/start-exam")
async def start_exam(session_id: str, exam_data: dict = None):
    """Start exam without restarting recording"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check verification status
    if sessions[session_id].get("verification_status") != "completed":
        raise HTTPException(
            status_code=400,
            detail="Verification not completed — cannot start exam yet"
        )

    # Use exam duration from session
    exam_duration = sessions[session_id].get("exam_duration", 60)
    exam_name = sessions[session_id].get("exam_name", "Unknown Exam")
    exam_id = sessions[session_id].get("exam_id")
    
    now = datetime.now().isoformat()

    # Update session with start time (recording continues uninterrupted)
    await update_session(session_id, {
        "started_at": now,
        "submission_status": "in_progress",
        "face_detection_status": "active",  
        "audio_monitoring_status": "active"
    })

    # Update in-memory session
    sessions[session_id].update({
        "started_at": now,
        "submission_status": "in_progress",
        "face_detection_status": "active",
        "audio_monitoring_status": "active"
    })

    print(f"✅ Exam started for session {session_id}. Recording continues from session start.")

    return {
        "status": "exam_started", 
        "exam_name": exam_name,
        "exam_duration": exam_duration,
        "exam_id": exam_id,
        "started_at": now
    }



# Add this after your existing session endpoints
@app.post("/api/sessions/{session_id}/sync-offline-data")
async def sync_offline_data(session_id: str, encrypted_data: List[dict]):
    """Sync encrypted offline data"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    results = []
    
    for encrypted_item in encrypted_data:
        try:
            # Decrypt data (simplified - use proper encryption in production)
            import base64
            decrypted_bytes = base64.b64decode(encrypted_item['encrypted_data'])
            decrypted_str = decrypted_bytes.decode('utf-8')
            decrypted_data = json.loads(decrypted_str)['data']
            
            # Process based on data type
            if decrypted_data['type'] == 'violation':
                await record_violation(session_id, Violation(
                    type=decrypted_data['violation_type'],
                    details=decrypted_data['details']
                ))
                
            elif decrypted_data['type'] == 'screenshot':
                screenshot_data = {
                    "id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "timestamp": decrypted_data['timestamp'],
                    "data": base64.b64decode(decrypted_data['image_data']),
                    "format": "jpeg"
                }
                await insert_screenshot(screenshot_data)
                
            results.append({"id": encrypted_item['id'], "status": "synced"})
            
        except Exception as e:
            results.append({"id": encrypted_item['id'], "status": "error", "error": str(e)})
    
    return {"synced_items": len(results), "results": results}


# Add this after your recording endpoints
@app.post("/api/sessions/{session_id}/recording-settings")
async def update_recording_settings(session_id: str, settings: dict):
    """Update recording settings based on bandwidth"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Update recording state with new settings
    if session_id in recording_state:
        recording_state[session_id].update({
            "fps": settings.get('fps', 10),
            "quality": settings.get('quality', 50),
            "resolution": settings.get('resolution', '1280x720')
        })
    
    return {"status": "settings_updated", "settings": settings}

@app.post("/api/sessions/{session_id}/screenshot-settings")
async def update_screenshot_settings(session_id: str, settings: dict):
    """Update screenshot settings based on bandwidth"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Restart screenshots with new settings if they're running
    if session_id in screenshot_tasks:
        screenshot_tasks[session_id].cancel()
        screenshot_config = ScreenshotConfig(
            interval=settings.get('interval', 5),
            quality=settings.get('quality', 70)
        )
        await start_screenshots_internal(session_id, screenshot_config)
    
    return {"status": "settings_updated", "settings": settings}


@app.post("/api/sessions/{session_id}/heartbeat")
async def receive_heartbeat(session_id: str, heartbeat_data: dict):
    """Receive heartbeat from client"""
    # Update session last activity timestamp
    await update_session(session_id, {
        "last_heartbeat": datetime.now().isoformat(),
        "bandwidth": heartbeat_data.get("bandwidth"),
        "quality_profile": heartbeat_data.get("profile")
    })
    return {"status": "heartbeat_received"}



@app.post("/api/sessions/{session_id}/behavioral-analysis")
async def receive_behavioral_data(session_id: str, behavior_data: dict):
    """Receive behavioral analysis data from client"""
    # Check if session exists
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")
    
    # Process behavioral data
    if behavior_data.get('type') == 'mouse_movement':
        await analyze_mouse_behavior(session_id, behavior_data)
    elif behavior_data.get('type') == 'keystroke':
        await analyze_keystroke_behavior(session_id, behavior_data)
    elif behavior_data.get('type') == 'focus_change':
        await analyze_focus_behavior(session_id, behavior_data)
    
    return {"status": "behavior_data_received"}


async def analyze_mouse_behavior(session_id: str, data: dict):
    """Analyze mouse movements for suspicious patterns"""
    # Implement mouse movement analysis logic
    suspicious_patterns = []
    
    # Example: Detect perfectly straight lines (automation)
    if data.get('straight_line_ratio', 0) > 0.3:
        suspicious_patterns.append('automated_mouse_movements')
    
    if suspicious_patterns:
        await record_violation(session_id, Violation(
            type="suspicious_mouse_behavior",
            details={
                "patterns": suspicious_patterns,
                "data": data,
                "timestamp": datetime.now().isoformat()
            }
        ))

async def analyze_keystroke_behavior(session_id: str, data: dict):
    """Analyze keystroke patterns"""
    # Implement keystroke analysis logic
    if data.get('variance', 100) < 10:  # Very consistent timing
        await record_violation(session_id, Violation(
            type="suspicious_typing_pattern",
            details={
                "pattern": "consistent_keystroke_timing",
                "variance": data.get('variance'),
                "timestamp": datetime.now().isoformat()
            }
        ))

async def analyze_focus_behavior(session_id: str, data: dict):
    """Analyze focus changes"""
    if data.get('changes_per_minute', 0) > 15:
        await record_violation(session_id, Violation(
            type="excessive_focus_changes",
            details={
                "changes_per_minute": data.get('changes_per_minute'),
                "timestamp": datetime.now().isoformat()
            }
        ))



@app.post("/api/sessions/{session_id}/bandwidth-report")
async def receive_bandwidth_report(session_id: str, report_data: dict):
    """Receive bandwidth reports from client"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")
    
    # Update session with bandwidth info
    await update_session(session_id, {
        "bandwidth_kbps": report_data.get('bandwidth'),
        "network_quality": report_data.get('quality'),
        "last_bandwidth_check": datetime.now().isoformat()
    })
    
    return {"status": "bandwidth_report_received"}


@app.post("/api/sessions/{session_id}/offline-data")
async def receive_offline_data(session_id: str, offline_data: dict):
    """Receive offline data from client"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")
    
    # Store offline data
    data_id = str(uuid.uuid4())
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            '''INSERT INTO offline_data (id, session_id, data_type, encrypted_data, timestamp)
            VALUES (?, ?, ?, ?, ?)''',
            (
                data_id,
                session_id,
                offline_data.get('type'),
                json.dumps(offline_data.get('data')),
                datetime.now().isoformat()
            )
        )
        await db.commit()
    
    return {"status": "offline_data_stored", "data_id": data_id}


@app.post("/api/sessions/{session_id}/status-update")
async def update_component_status(session_id: str, status_data: dict):
    """Update component status from client"""
    component = status_data.get('component')
    status = status_data.get('status')
    message = status_data.get('message', '')
    
    update_fields = {}
    
    if component == 'behavioral_analysis':
        update_fields['behavioral_analysis_status'] = status
    elif component == 'offline_mode':
        update_fields['offline_mode_status'] = status
    elif component == 'bandwidth':
        update_fields['bandwidth_status'] = status
    
    if update_fields:
        await update_session(session_id, update_fields)
    
    return {"status": "component_status_updated"}




@app.delete("/api/sessions/{session_id}")
async def end_session(session_id: str):
    """End session and stop all monitoring including recording"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Set end status
    termination_reason = "Normal completion"
    submission_status = "completed"
    
    # Check if terminated due to violations
    if sessions[session_id].get("total_violations", 0) >= 5:
        submission_status = "terminated"
        termination_reason = "Exceeded maximum violations"
    
    await update_exam_end(session_id, submission_status, termination_reason)
    
    # Stop recording
    await stop_recording(session_id)
    
    # Remove session from memory
    if session_id in sessions:
        del sessions[session_id]

    # Remove remote detector
    if session_id in remote_detectors:
        del remote_detectors[session_id]
    
    return {"status": "session_ended", "recording_stopped": True}


# Get session data for review
@app.get("/api/sessions/{session_id}/review")
async def get_session_review(session_id: str):
    # Get session data from database
    async with aiosqlite.connect(DATABASE_NAME) as db:
        # Get session info
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,)
        )
        session_row = await cursor.fetchone()
        
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Get violations
        cursor = await db.execute(
            "SELECT * FROM violations WHERE session_id = ? ORDER BY timestamp",
            (session_id,)
        )
        violation_rows = await cursor.fetchall()
        
        # Get screenshots
        cursor = await db.execute(
            "SELECT * FROM screenshots WHERE session_id = ? ORDER by timestamp",
            (session_id,)
        )
        screenshot_rows = await cursor.fetchall()
        
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
    
    # Format session data
    session_data = {
        "id": session_row[0],
        "created_at": session_row[1],
        "student_id": session_row[2],
        "student_name": session_row[3],
        "exam_name": session_row[4],
        "exam_duration": session_row[5],
        "verification_status": session_row[6],
        "behavioral_analysis_status": "initializing",
        "offline_mode_status": "initializing", 
        "bandwidth_status": "initializing",
        "bandwidth_kbps": 0,
        "network_quality": "unknown",
        "last_bandwidth_check": None,
        "mobile_connected": bool(session_row[7]),
        "mobile_status": session_row[8],
        "face_detection_status": session_row[9],
        "audio_monitoring_status": session_row[10],
        "behavior_monitoring_status": session_row[11],
        "verification_attempts": session_row[12],
        "started_at": session_row[13],
        "ended_at": session_row[14],
        "submission_status": session_row[15],
        "termination_reason": session_row[16],
        "total_violations": session_row[17],
        "severity_level": session_row[18],
        "review_status": session_row[19]
    }
    
    # Format violations
    violations = []
    for row in violation_rows:
        violations.append({
            "id": row[0],
            "session_id": row[1],
            "type": row[2],
            "timestamp": row[3],
            "details": json.loads(row[4]) if row[4] else {}
        })
    
    # Format screenshots
    screenshots = []
    for row in screenshot_rows:
        # Convert binary data to base64 for response
        image_data = base64.b64encode(row[3]).decode('utf-8') if row[3] else None
        screenshots.append({
            "id": row[0],
            "session_id": row[1],
            "timestamp": row[2],
            "image_data": image_data,
            "format": row[4]
        })
    
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
            "frames": json.loads(row[7]) if row[7] else []
        })
    
    # Format ID images
    id_images = []
    for row in id_image_rows:
        # Convert binary data to base64 for response
        image_data = base64.b64encode(row[2]).decode('utf-8') if row[2] else None
        id_images.append({
            "id": row[0],
            "session_id": row[1],
            "image_data": image_data,
            "upload_time": row[3]
        })
    
    # Format face images
    face_images = []
    for row in face_image_rows:
        # Convert binary data to base64 for response
        image_data = base64.b64encode(row[2]).decode('utf-8') if row[2] else None
        face_images.append({
            "id": row[0],
            "session_id": row[1],
            "image_data": image_data,
            "capture_time": row[3]
        })
    
    return {
        "session": session_data,
        "violations": violations,
        "screenshots": screenshots,
        "recordings": recordings,
        "id_images": id_images,
        "face_images": face_images
    }





@app.get("/api/sessions")
async def get_all_sessions(exam_id: Optional[str] = None, limit: int = 100, offset: int = 0):
    """Get all sessions with optional filtering by exam"""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        if exam_id:
            cursor = await db.execute('''
                SELECT s.id, s.created_at, s.student_id, s.student_name, 
                       s.exam_name, s.exam_duration, s.verification_status,
                       s.mobile_connected, s.mobile_status, s.face_detection_status,
                       s.audio_monitoring_status, s.behavior_monitoring_status,
                       s.verification_attempts, s.started_at, s.ended_at,
                       s.submission_status, s.termination_reason, s.total_violations,
                       s.severity_level, s.review_status, s.exam_id,
                       e.name as exam_name_from_join, e.duration as exam_duration_from_join
                FROM sessions s
                LEFT JOIN exam_sessions es ON s.id = es.session_id
                LEFT JOIN exams e ON es.exam_id = e.id
                WHERE es.exam_id = ?
                ORDER BY s.created_at DESC
                LIMIT ? OFFSET ?
            ''', (exam_id, limit, offset))
        else:
            cursor = await db.execute('''
                SELECT s.id, s.created_at, s.student_id, s.student_name, 
                       s.exam_name, s.exam_duration, s.verification_status,
                       s.mobile_connected, s.mobile_status, s.face_detection_status,
                       s.audio_monitoring_status, s.behavior_monitoring_status,
                       s.verification_attempts, s.started_at, s.ended_at,
                       s.submission_status, s.termination_reason, s.total_violations,
                       s.severity_level, s.review_status, s.exam_id,
                       e.name as exam_name_from_join, e.duration as exam_duration_from_join
                FROM sessions s
                LEFT JOIN exam_sessions es ON s.id = es.session_id
                LEFT JOIN exams e ON es.exam_id = e.id
                ORDER BY s.created_at DESC
                LIMIT ? OFFSET ?
            ''', (limit, offset))
        
        rows = await cursor.fetchall()
        sessions_list = []
        for row in rows:
            # Use exam data from join if available, otherwise use session data
            exam_name = row[21] if len(row) > 21 and row[21] else row[4]
            exam_duration = row[22] if len(row) > 22 and row[22] else row[5]
            
            sessions_list.append({
                "id": row[0],
                "created_at": row[1],
                "student_id": row[2],
                "student_name": row[3],
                "exam_name": exam_name,
                "exam_duration": exam_duration,
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
                "total_violations": row[17],  # Only total violations
                "severity_level": row[18],
                "review_status": row[19],
                "exam_id": row[20] if len(row) > 20 else None
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
            "sessions": sessions_list,
            "total_count": total_count,
            "limit": limit,
            "offset": offset
        }
    
    
# Bandwidth allocation at 256 kbps (constrained scenario)
def allocate_bandwidth_256k():
    return {
        'screen_recording': {
            'fps': 3,
            'resolution': '640x480', 
            'quality': 30,
            'estimated_bandwidth': 100,  # kbps (39%)
        },
        'screenshots': {
            'interval': 30,  # seconds
            'quality': 40,
            'estimated_bandwidth': 3,  # kbps (1%)
        },
        'face_camera': {
            'resolution': '320x240',
            'fps': 5,
            'estimated_bandwidth': 50,  # kbps (20%)
        },
        'audio_monitoring': {
            'sample_rate': 8000,
            'channels': 1,
            'estimated_bandwidth': 8,  # kbps (3%)
        },
        'data_communication': {
            'heartbeat_interval': 60,  # seconds
            'batch_violations': True,
            'estimated_bandwidth': 5,  # kbps (2%)
        },
        'bandwidth_buffer': {
            'reserved': 90,  # kbps (35% buffer for fluctuations)
        },
        'total_estimated': 256  # kbps (100%)
    }



# Student management endpoints
@app.get("/api/students")
async def get_all_students(limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT id, name, email, created_at FROM students ORDER BY name LIMIT ? OFFSET ?",
            (limit, offset)
        )
        rows = await cursor.fetchall()
        
        students = []
        for row in rows:
            students.append({
                "id": row[0],
                "name": row[1],
                "email": row[2],
                "created_at": row[3]
            })
        
        # Get total count
        cursor = await db.execute("SELECT COUNT(*) FROM students")
        total_count = (await cursor.fetchone())[0]
    
    return {
        "students": students,
        "total_count": total_count,
        "limit": limit,
        "offset": offset
    }

@app.post("/api/students")
async def create_student(student_data: dict):
    try:
        student_id = student_data.get("id")
        name = student_data.get("name")
        email = student_data.get("email")
        
        if not student_id or not name:
            raise HTTPException(status_code=400, detail="Student ID and name are required")
        
        async with aiosqlite.connect(DATABASE_NAME) as db:
            await db.execute(
                "INSERT INTO students (id, name, email, created_at) VALUES (?, ?, ?, ?)",
                (student_id, name, email, datetime.now().isoformat())
            )
            await db.commit()
        
        return {"status": "created", "student_id": student_id}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating student: {str(e)}")


@app.post("/api/debug/face-detection")
async def debug_face_detection(file: UploadFile = File(...)):
    """Debug endpoint to test face detection"""
    try:
        contents = await file.read()
        
        # Test face detection
        has_face, face_count = detect_faces(contents)
        
        # Try to draw boxes on the image for visualization
        nparr = np.frombuffer(contents, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if has_face:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            faces = face_cascade.detectMultiScale(gray, 1.05, 6, minSize=(50, 50))
            
            # Draw rectangles around faces
            for (x, y, w, h) in faces:
                cv2.rectangle(image, (x, y), (x+w, y+h), (0, 255, 0), 2)
            
            # Convert back to bytes
            _, img_encoded = cv2.imencode('.jpg', image)
            annotated_image = img_encoded.tobytes()
            
            return {
                "faces_detected": face_count,
                "annotated_image": base64.b64encode(annotated_image).decode('utf-8'),
                "message": f"Found {face_count} face(s)"
            }
        else:
            return {
                "faces_detected": 0,
                "message": "No faces detected"
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Debug error: {str(e)}")


# Add these endpoints to your FastAPI app

@app.head("/api/ping")
async def ping():
    """Simple ping endpoint for connectivity testing"""
    return {"status": "ok"}



@app.head("/api/ping")
async def ping_head():
    """Lightweight HEAD ping for client-side latency checks."""
    return Response(status_code=200)


@app.get("/api/ping")
async def ping_get():
    return {"status": "ok"}


@app.get("/api/bandwidth-test")
async def bandwidth_test(size: int = 100000):
    """Bandwidth test endpoint - returns dummy data of specified size"""
    # Return a string of the requested size
    dummy_data = "x" * size
    return Response(content=dummy_data, media_type="text/plain")

@app.post("/api/sessions/{session_id}/window-analysis")
async def window_analysis(session_id: str, window_data: dict):
    """Receive window analysis data from client"""
    # Check if session exists
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")
    
    # Analyze window behavior for violations
    await analyze_window_behavior(session_id, window_data)
    
    return {"status": "window_analysis_received"}

@app.post("/api/sessions/{session_id}/recording-quality")
async def update_recording_quality(session_id: str, quality_settings: dict):
    """Update recording quality settings"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Update recording state with new settings
    if session_id in recording_state:
        recording_state[session_id].update({
            "fps": quality_settings.get('fps', 10),
            "quality": quality_settings.get('quality', 50),
            "resolution": quality_settings.get('resolution', '1280x720')
        })
    
    return {"status": "recording_quality_updated", "settings": quality_settings}



@app.post("/api/sessions/{session_id}/start-remote-detection")
async def start_remote_detection(session_id: str):
    """Start remote control detection for a session"""
    if session_id not in remote_detectors:
        remote_detectors[session_id] = RemoteControlDetector(session_id)
        await remote_detectors[session_id].start_detection()
        
    return {"status": "remote_detection_started"}

@app.get("/api/sessions/{session_id}/remote-detection-status")
async def get_remote_detection_status(session_id: str):
    """Get current remote detection status"""
    if session_id in remote_detectors:
        score = await remote_detectors[session_id].evaluate_suspicion()
        return {
            "status": "active",
            "suspicion_score": score,
            "timing_score": remote_detectors[session_id].timing_detector.suspicion_score,
            "input_score": remote_detectors[session_id].input_detector.suspicion_score,
            "focus_score": remote_detectors[session_id].focus_detector.suspicion_score,
            "non_keyboard_violations": remote_detectors[session_id].non_keyboard_monitor.violation_count,
            "exam_terminated": remote_detectors[session_id].non_keyboard_monitor.exam_terminated
        }
    else:
        return {"status": "inactive", "suspicion_score": 0, "non_keyboard_violations": 0, "exam_terminated": False}

@app.post("/api/sessions/{session_id}/track-non-keyboard-input")
async def track_non_keyboard_input(session_id: str, input_data: dict):
    """Track non-keyboard input events"""
    if session_id not in remote_detectors:
        return {"status": "detector_not_active"}
    
    await remote_detectors[session_id].track_user_interaction(
        'non_keyboard_input',
        input_type=input_data.get('type', 'unknown'),
        data=input_data.get('data', ''),
        input_method=input_data.get('method', 'unknown')
    )
    
    return {
        "status": "tracked", 
        "violation_count": remote_detectors[session_id].non_keyboard_monitor.violation_count,
        "exam_terminated": remote_detectors[session_id].non_keyboard_monitor.exam_terminated
    }

# Add this function for window behavior analysis
async def analyze_window_behavior(session_id: str, window_data: dict):
    """Analyze window behavior for suspicious activity"""
    suspicious_patterns = []
    
    # Detect floating browsers (small, always-on-top windows)
    if (window_data.get('width', 0) < 400 and 
        window_data.get('height', 0) < 300 and 
        window_data.get('alwaysOnTop', False) and 
        'browser' in window_data.get('title', '').lower()):
        suspicious_patterns.append('floating_browser')
    
    # Detect rapid window switching
    if window_data.get('focusChanges', 0) > 10:  # More than 10 changes per minute
        suspicious_patterns.append('excessive_focus_changes')
    
    # Detect off-screen windows
    window_x = window_data.get('x', 0)
    window_y = window_data.get('y', 0)
    window_width = window_data.get('width', 0)
    window_height = window_data.get('height', 0)
    
    if (window_x + window_width < 0 or 
        window_y + window_height < 0):
        suspicious_patterns.append('offscreen_window')
    
    if suspicious_patterns:
        await record_violation(session_id, Violation(
            type="suspicious_window_behavior",
            details={
                "patterns": suspicious_patterns,
                "window_data": window_data,
                "timestamp": datetime.now().isoformat()
            }
        ))


async def start_exam_timer(session_id: str):
    """Start the exam timer based on the specific exam duration"""
    if session_id not in sessions:
        return
    
    exam_duration_minutes = sessions[session_id].get("exam_duration", 60)
    exam_duration_seconds = exam_duration_minutes * 60
    
    start_time = datetime.now()
    end_time = start_time + timedelta(minutes=exam_duration_minutes)
    
    # Store timer information
    sessions[session_id]["timer_info"] = {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_minutes": exam_duration_minutes,
        "remaining_seconds": exam_duration_seconds
    }
    
    # Update frontend about timer start (you'll need to implement WebSocket communication)
    # This would send timer updates to the frontend
    
    # Start background task to monitor time
    asyncio.create_task(monitor_exam_time(session_id, exam_duration_seconds))

async def monitor_exam_time(session_id: str, total_seconds: int):
    """Monitor exam time and auto-submit when time is up"""
    remaining = total_seconds
    
    while remaining > 0 and session_id in sessions:
        if sessions[session_id].get("submission_status") != "in_progress":
            break
            
        await asyncio.sleep(1)
        remaining -= 1
        
        # Update remaining time in session
        if session_id in sessions and "timer_info" in sessions[session_id]:
            sessions[session_id]["timer_info"]["remaining_seconds"] = remaining
        
        # Send time update to frontend via WebSocket (implement this)
        # await send_time_update(session_id, remaining)
        
        # Warning when 5 minutes remaining
        if remaining == 300:  # 5 minutes
            await send_exam_warning(session_id, "5 minutes remaining")
        
        # Warning when 1 minute remaining  
        if remaining == 60:  # 1 minute
            await send_exam_warning(session_id, "1 minute remaining")
    
    # Time's up - auto-submit
    if session_id in sessions and sessions[session_id].get("submission_status") == "in_progress":
        await update_exam_end(session_id, "submitted", "Time expired")
        await send_exam_warning(session_id, "Time's up! Exam auto-submitted.")

async def send_exam_warning(session_id: str, message: str):
    """Send warning message to frontend"""
    # Implement WebSocket message sending to frontend
    print(f"Exam warning for session {session_id}: {message}")
    # You'll need to implement actual WebSocket communication here

    

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Secure Exam Proctoring</title>
    <script src="https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@3.18.0/dist/tf.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/@tensorflow-models/face-landmarks-detection@0.0.3/dist/face-landmarks-detection.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/qrcode@1.5.1/build/qrcode.min.js"></script>
    <style>
        :root {
            --primary-color: #3498db;
            --danger-color: #e74c3c;
            --warning-color: #f39c12;
            --success-color: #2ecc71;
            --dark-color: #2c3e50;
            --light-color: #ecf0f1;
            --text-color: #333;
            --text-light: #7f8c8d;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #f5f5f5;
            margin: 0;
            padding: 0;
            color: var(--text-color);
            line-height: 1.6;
        }
        
        .container {
            background-color: white;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
            width: 100%;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        
        .status-bar {
            background-color: var(--dark-color);
            color: white;
            padding: 12px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .status-item {
            display: flex;
            align-items: center;
            margin: 0 10px;
        }
        
        .status-label {
            margin-right: 5px;
            font-weight: 600;
        }
        
        .status-value {
            font-weight: bold;
        }
        
        #violation-count {
            color: var(--danger-color);
        }
        
        #timer {
            font-family: monospace;
        }
        
        .content-area {
            padding: 20px;
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        
        .exam-content {
            flex: 1;
            padding: 20px;
            margin-bottom: 20px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
        }
        
        .question {
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid #eee;
        }
        
        .question:last-child {
            border-bottom: none;
        }
        
        input[type="text"] {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 16px;
            margin-top: 10px;
        }
        
        .proctoring-section {
            margin-top: 20px;
            padding: 20px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
        }
        
        .camera-container {
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            margin: 20px 0;
        }
        
        .video-feed {
            flex: 1;
            min-width: 300px;
            background: #000;
            border-radius: 8px;
            overflow: hidden;
            position: relative;
        }
        
        .video-feed video {
            width: 100%;
            display: block;
        }
        
        .video-label {
            background: rgba(0, 0, 0, 0.7);
            color: white;
            padding: 8px;
            text-align: center;
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            z-index: 10;
        }
        
        .proctoring-status {
            margin: 10px 0;
            padding: 15px;
            border-radius: 8px;
            font-weight: bold;
            text-align: left;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .status-initializing {
            background-color: rgba(255, 193, 7, 0.1);
            color: #856404;
            border-left: 4px solid #ffc107;
        }
        
        .status-active {
            background-color: rgba(40, 167, 69, 0.1);
            color: #155724;
            border-left: 4px solid #28a745;
        }
        
        .status-warning {
            background-color: rgba(255, 193, 7, 0.1);
            color: #856404;
            border-left: 4px solid #ffc107;
        }
        
        .status-danger {
            background-color: rgba(220, 53, 69, 0.1);
            color: #721c24;
            border-left: 4px solid #dc3545;
        }
        
        .status-disconnected {
            background-color: rgba(108, 117, 125, 0.1);
            color: #383d41;
            border-left: 4px solid #6c757d;
        }
        
        .status-details {
            font-size: 0.9em;
            margin-top: 4px;
            color: inherit;
            opacity: 0.8;
            font-weight: normal;
        }
        
        .detection-box {
            position: absolute;
            border: 2px solid #00FF00;
            background: rgba(0, 255, 0, 0.1);
            z-index: 100;
        }
        
        .qr-container {
            margin: 20px auto;
            padding: 20px;
            background: #f9f9f9;
            border-radius: 8px;
            text-align: center;
            max-width: 300px;
        }
        
        #qr-code {
            margin: 0 auto;
            display: block;
        }
        
        button {
            background-color: var(--primary-color);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 16px;
            transition: all 0.3s;
            margin: 10px 0;
            font-weight: 600;
        }
        
        button:hover {
            background-color: #2980b9;
            transform: translateY(-1px);
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.2);
        }
        
        button:disabled {
            background-color: #95a5a6;
            cursor: not-allowed;
        }
        
        .warning {
            color: var(--danger-color);
            font-weight: bold;
            margin: 20px 0;
            padding: 15px;
            background-color: rgba(231, 76, 60, 0.1);
            border-left: 4px solid var(--danger-color);
            border-radius: 4px;
        }
        
        .fullscreen-warning {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.95);
            color: white;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            font-size: 24px;
            z-index: 1000;
            display: none;
        }
        
        #countdown {
            font-size: 72px;
            font-weight: bold;
            margin: 20px 0;
            color: var(--danger-color);
        }
        
        .pulse {
            animation: pulse 0.5s;
        }
        
        @keyframes pulse {
            0% { transform: scale(1); }
            50% { transform: scale(1.2); }
            100% { transform: scale(1); }
        }
        
        #welcome-screen, #exam-screen {
            padding: 20px;
            max-width: 800px;
            margin: 0 auto;
            width: 100%;
        }
        
        #welcome-screen {
            display: block;
        }
        
        #exam-screen {
            display: none;
        }
        
        .instructions {
            background: rgba(52, 152, 219, 0.1);
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
            border-left: 4px solid var(--primary-color);
        }
        
        .instructions ul {
            padding-left: 20px;
        }
        
        .instructions li {
            margin-bottom: 10px;
        }
        
        h1, h2, h3 {
            color: var(--dark-color);
        }
        
        h1 {
            margin-top: 0;
        }
        
        .verification-section {
            margin: 20px 0;
            padding: 20px;
            background: #f9f9f9;
            border-radius: 8px;
            border-left: 4px solid var(--primary-color);
        }
        
        .verification-step {
            margin-bottom: 15px;
            padding: 10px;
            background: white;
            border-radius: 6px;
            border: 1px solid #eee;
        }
        
        .verification-status {
            padding: 10px;
            border-radius: 6px;
            margin-top: 10px;
            font-weight: bold;
        }
        
        .verified {
            background-color: rgba(46, 204, 113, 0.1);
            color: #155724;
            border: 1px solid #28a745;
        }
        
        .pending {
            background-color: rgba(255, 193, 7, 0.1);
            color: #856404;
            border: 1px solid #ffc107;
        }
        
        .failed {
            background-color: rgba(220, 53, 69, 0.1);
            color: #721c24;
            border: 1px solid #dc3545;
        }
        
        .file-upload {
            margin: 10px 0;
            padding: 15px;
            border: 2px dashed #ddd;
            border-radius: 6px;
            text-align: center;
            cursor: pointer;
        }
        
        .file-upload:hover {
            border-color: var(--primary-color);
        }
        
        .step-indicator {
            display: flex;
            justify-content: space-between;
            margin: 20px 0;
        }
        
        .step {
            flex: 1;
            text-align: center;
            padding: 10px;
            background: #f0f0f0;
            border-radius: 5px;
            margin: 0 5px;
        }
        
        .step.active {
            background: var(--primary-color);
            color: white;
        }
        
        .step.completed {
            background: var(--success-color);
            color: white;
        }
        
        .preview-image {
            max-width: 100%;
            max-height: 200px;
            margin: 10px 0;
            border-radius: 5px;
        }
        
        @media (max-width: 768px) {
            .camera-container {
                flex-direction: column;
            }
            
            .video-feed {
                width: 100%;
            }
            
            .status-bar {
                flex-direction: column;
                align-items: flex-start;
            }
            
            .status-item {
                margin: 5px 0;
            }
            
            .step-indicator {
                flex-direction: column;
            }
            
            .step {
                margin: 5px 0;
            }
        }
    </style>
</head>
<body>
    <div class="container" id="main-container">
        <body>
    <div class="container" id="main-container">
        <div id="welcome-screen">
            <h1>Welcome to Online Exam</h1>
            <div class="instructions">
                <h2>Exam Instructions</h2>
                <p>Before starting your exam, please read all instructions carefully:</p>
                <ul>
                    <li><strong>Identity Verification:</strong> You must complete the identity verification process before starting the exam</li>
                    <li><strong>Technical Requirements:</strong> Ensure you have a stable internet connection, webcam, and microphone</li>
                    <li><strong>Environment:</strong> Find a quiet, well-lit room without distractions</li>
                    <li><strong>Prohibited Items:</strong> No books, notes, phones, or other electronic devices are allowed</li>
                    <li><strong>Browser Restrictions:</strong> Do not switch tabs or open new windows during the exam</li>
                    <li><strong>Full Screen Mode:</strong> The exam must be taken in full screen mode</li>
                    <li><strong>Camera Monitoring:</strong> Your webcam must remain on throughout the exam</li>
                    <li><strong>Audio Monitoring:</strong> Your microphone will be active to detect any suspicious noises</li>
                    <li><strong>Time Limit:</strong> The exam has a strict time limit - you cannot pause once started</li>
                    <li><strong>Academic Integrity:</strong> Any violation of exam rules will result in immediate termination</li>
                </ul>
                <p class="warning">⚠️ Warning: Any attempt to bypass security measures will be recorded and may result in exam termination.</p>
            </div>
            
            <div class="step-indicator">
                <div class="step active" id="step1">1. Student Info</div>
                <div class="step" id="step2">2. ID Verification</div>
                <div class="step" id="step3">3. Face Verification</div>
                <div class="step" id="step4">4. Exam Ready</div>
            </div>
            
            <div class="verification-section">
            <!-- Step 1: Student Information -->
                <div class="verification-step" id="info-step">
                    <h3>Step 1: Enter Your Student Information</h3>
                    <div>
                        <label for="student-id">Student ID:</label>
                        <input type="text" id="student-id" placeholder="Enter your student ID" required>
                    </div>
                    <div>
                        <label for="student-name">Full Name:</label>
                        <input type="text" id="student-name" placeholder="Enter your full name as on ID" required>
                    </div>
                    <div>
                        <label for="exam-id">Exam ID (if provided):</label>
                        <input type="text" id="exam-id" placeholder="Enter exam ID if provided by instructor">
                    </div>
                    <button id="verify-info-btn">Verify Information</button>
                    <div id="info-status" class="verification-status pending" style="display: none;"></div>
                </div>
                
                <!-- Step 2: ID Card Upload (UPLOAD, not camera) -->
                <div class="verification-step" id="id-step" style="display: none;">
                    <h3>Step 2: Upload Your ID Card</h3>
                    <p>Please upload a clear photo of your student ID card</p>
                    <div class="file-upload" id="id-upload">
                        <p>📷 Click to upload ID card photo</p>
                        <input type="file" id="id-file" accept="image/*" style="display: none;">
                    </div>
                    <div id="id-preview" style="display: none;">
                        <img id="id-preview-image" class="preview-image" src="" alt="ID Preview">
                        <button id="retake-id">Upload Different Photo</button>
                        <button id="confirm-id">Confirm ID Photo</button>
                    </div>
                    <div id="id-status" class="verification-status pending" style="display: none;"></div>
                </div>
                
                <!-- Step 3: Face Capture (CAMERA) -->
                <div class="verification-step" id="face-step" style="display: none;">
                    <h3>Step 3: Face Verification</h3>
                    <p>Please take a clear photo of your face for verification. Ensure good lighting and look directly at the camera.</p>
                    <div class="camera-container">
                        <div class="video-feed">
                            <video id="face-camera" autoplay playsinline></video>
                            <div class="video-label">Face Camera</div>
                            <div id="face-detection-preview"></div>
                        </div>
                    </div>
                    <button id="capture-face">Capture Face Photo</button>
                    <div id="face-preview" style="display: none;">
                        <img id="face-preview-image" class="preview-image" src="" alt="Face Preview">
                        <button id="retake-face">Retake Photo</button>
                        <button id="confirm-face">Verify Face</button>
                    </div>
                    <div id="face-status" class="verification-status pending" style="display: none;"></div>
                </div>
                
                <!-- Step 4: Verification Complete -->
                <div class="verification-step" id="complete-step" style="display: none;">
                    <h3>Step 4: Verification Complete</h3>
                    <div id="verification-result" class="verification-status verified">
                        <p>✅ Identity verified successfully!</p>
                        <p>You can now proceed to the exam.</p>
                    </div>
                    <button id="start-exam-btn">Start Exam</button>
                </div>
            </div> 
        </div>
        
        <div id="exam-screen">
            <div class="status-bar" id="status-bar">
                <div class="status-item">
                    <span class="status-label">Lockdown:</span>
                    <span class="status-value" id="lock-status">ACTIVE</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Violations:</span>
                    <span class="status-value" id="violation-count">0</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Time:</span>
                    <span class="status-value" id="timer">00:00:00</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Verification:</span>
                    <span class="status-value" id="verification-status">Verified</span>
                </div>
            </div>
            
            <div class="content-area">
                <h1>Exam in Progress</h1>
                
                <div class="exam-content" id="exam-content">
                    <p>This is your exam content. Answer all questions below:</p>
                    
                    <div class="question" id="question1">
                        <h3>Question 1</h3>
                        <p>What is the capital of France?</p>
                        <input type="text" id="answer1" placeholder="Your answer">
                    </div>
                    
                    <div class="question" id="question2">
                        <h3>Question 2</h3>
                        <p>Solve for x: 2x + 5 = 15</p>
                        <input type="text" id="answer2" placeholder="Your answer">
                    </div>
                    
                    <div class="question" id="question3">
                        <h3>Question 3</h3>
                        <p>Explain the theory of relativity in your own words:</p>
                        <textarea id="answer3" placeholder="Your answer" rows="4" style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px;"></textarea>
                    </div>
                </div>

                <div class="proctoring-section">
                    <h2>Proctoring Status</h2>
                    <div class="camera-container">
                        <div class="video-feed">
                            <video id="primary-camera" autoplay playsinline></video>
                            <div class="video-label">Primary Camera</div>
                            <div id="face-detection-container"></div>
                            <div id="primary-status" class="proctoring-status status-initializing">
                                <div>
                                    <span>Face Detection:</span>
                                    <span class="status-value">Initializing</span>
                                    <div class="status-details">Loading face detection model</div>
                                </div>
                            </div>
                        </div>
                        <div class="video-feed" id="secondary-camera-feed">
                            <div id="mobile-pairing">
                                <p>Pair your mobile device as a secondary camera:</p>
                                <div class="qr-container">
                                    <canvas id="qr-code"></canvas>
                                </div>
                                <p>Scan this QR code with the proctoring mobile app</p>
                                <div id="mobile-status" class="proctoring-status status-disconnected">
                                    <div>
                                        <span>Mobile Camera:</span>
                                        <span class="status-value">Disconnected</span>
                                        <div class="status-details">Waiting for connection</div>
                                    </div>
                                </div>
                            </div>
                            <video id="secondary-camera" autoplay playsinline style="display:none;"></video>
                            <div class="video-label" style="display:none;">Secondary Camera</div>
                        </div>
                    </div>
                    <div id="audio-status" class="proctoring-status status-initializing">
                        <div>
                            <span>Audio Monitoring:</span>
                            <span class="status-value">Initializing</span>
                            <div class="status-details">Setting up audio analyzer</div>
                        </div>
                    </div>
                    <div id="behavior-status" class="proctoring-status status-initializing">
                        <div>
                            <span>Behavior Analysis:</span>
                            <span class="status-value">Initializing</span>
                            <div class="status-details">Preparing gaze detection</div>
                        </div>
                    </div>
                    <div id="system-status" class="proctoring-status status-initializing">
                        <div>
                            <span>System Status:</span>
                            <span class="status-value">Initializing</span>
                            <div class="status-details">Loading all components</div>
                        </div>
                    </div>
                    <div id="monitor-status" class="proctoring-status status-initializing">
                        <div>
                            <span>Monitor Check:</span>
                            <span class="status-value">Initializing</span>
                            <div class="status-details">Checking display configuration</div>
                        </div>
                    </div>
                    <div id="screenshot-status" class="proctoring-status status-initializing">
                        <div>
                            <span>Screen Capture:</span>
                            <span class="status-value">Initializing</span>
                            <div class="status-details">Preparing screen capture</div>
                        </div>
                    </div>

                    
                    <div id="behavioral-status" class="proctoring-status status-initializing">
                        <div>
                            <span>Behavioral Analysis:</span>
                            <span class="status-value">Initializing</span>
                            <div class="status-details">Analyzing patterns...</div>
                        </div>
                    </div>

                    <div id="offline-status" class="proctoring-status status-initializing">
                        <div>
                            <span>Offline Mode:</span>
                            <span class="status-value">Initializing</span>
                            <div class="status-details">Monitoring connectivity...</div>
                        </div>
                    </div>

                    <div id="bandwidth-status" class="proctoring-status status-initializing">
                        <div>
                            <span>Bandwidth:</span>
                            <span class="status-value">Initializing</span>
                            <div class="status-details">Measuring connection...</div>
                        </div>
                    </div>

                </div> 
                <button id="submit-btn">Submit Exam</button>
                <p class="warning" id="warning">Warning: Any attempt to bypass security measures will be recorded and may result in exam termination.</p>
            </div>
        </div>
    </div>
    
    <div class="fullscreen-warning" id="fullscreen-warning">
        <h2>⚠️ Fullscreen Required!</h2>
        <div class="instructions" style="text-align: center; max-width: 500px;">
            <h3>To return to fullscreen:</h3>
            <p><strong>Windows/Linux:</strong> Press <kbd>F11</kbd></p>
            <p><strong>Mac:</strong> Press <kbd>⌘ Command</kbd> + <kbd>⌃ Control</kbd> + <kbd>F</kbd></p>
            <p>Or click the button below:</p>
        </div>
        <div id="countdown">5</div>
        <p>Your exam will auto-submit in:</p>
        <button id="retry-fullscreen">Click Here to Retry Fullscreen</button>
    </div>

    <script>
        // Configuration
        const config = {
            faceDetectionInterval: 1000,
            audioAnalysisInterval: 1000,
            gazeDetectionInterval: 1000,
            maxViolationsBeforeTermination: 5,
            minFaceDetectionConfidence: 0.5,
            statusUpdateInterval: 2000,
            heartbeatInterval: 5000,
            fullscreenCheckInterval: 1000,
            monitorCheckInterval: 10000,
            screenshotInterval: 5000, // 5 seconds between screenshots
            recordingFPS: 10 // Frames per second for recording
        };

        // State
        let state = {
            sessionId: null,
            bandwidth: 0,
            networkQuality: 'unknown',
            behavioralPatterns: 0,
            offlineItems: 0,
            faceDetectionCount: 0,
            audioLevel: 0,
            screenshotsTaken: 0,
            violations: 0,
            examDuration: 0,
            timerInterval: null,
            countdownInterval: null,
            countdown: 5,
            examStarted: false,
            examEnded: false,
            faceDetectionModel: null,
            audioContext: null,
            analyser: null,
            mediaStreamSource: null,
            primaryStream: null,
            secondaryStream: null,
            mobileSocket: null,
            lastFacePosition: null,
            gazeDirection: null,
            lastAudioLevel: 0,
            mobileConnected: false,
            isInFullscreen: false,
            initialResizeHandled: false,
            mobileHeartbeat: null,
            fullscreenCheckInterval: null,
            monitorCheckInterval: null,
            lastViolationTime: null,
            lastMonitorCount: 1,
            monitorViolationDetected: false,
            screenshotInterval: null,
            recordingInterval: null,
            verification: {
                currentStep: 1,
                studentId: null,
                studentName: null,
                status: 'not_started'
            },
            idCameraStream: null,
            faceCameraStream: null
        };

        // DOM Elements
        const elements = {
            // Verification elements
            step1: document.getElementById('step1'),
            step2: document.getElementById('step2'),
            step3: document.getElementById('step3'),
            step4: document.getElementById('step4'),
            infoStep: document.getElementById('info-step'),
            idStep: document.getElementById('id-step'),
            faceStep: document.getElementById('face-step'),
            completeStep: document.getElementById('complete-step'),
            studentIdInput: document.getElementById('student-id'),
            studentNameInput: document.getElementById('student-name'),
            verifyInfoBtn: document.getElementById('verify-info-btn'),
            infoStatus: document.getElementById('info-status'),
            faceCamera: document.getElementById('face-camera'),
            captureFaceBtn: document.getElementById('capture-face'),
            idPreview: document.getElementById('id-preview'),
            idPreviewImage: document.getElementById('id-preview-image'),
            retakeId: document.getElementById('retake-id'),
            confirmId: document.getElementById('confirm-id'),
            idStatus: document.getElementById('id-status'),
            facePreview: document.getElementById('face-preview'),
            facePreviewImage: document.getElementById('face-preview-image'),
            retakeFace: document.getElementById('retake-face'),
            confirmFace: document.getElementById('confirm-face'),
            faceStatus: document.getElementById('face-status'),
            startExamBtn: document.getElementById('start-exam-btn'),
            faceDetectionPreview: document.getElementById('face-detection-preview'),
            idUpload: document.getElementById('id-upload'),
            idFile: document.getElementById('id-file'),
            
            // Exam elements
            startBtn: document.getElementById('start-btn'),
            welcomeScreen: document.getElementById('welcome-screen'),
            examScreen: document.getElementById('exam-screen'),
            statusBar: document.getElementById('status-bar'),
            violationCountEl: document.getElementById('violation-count'),
            timerEl: document.getElementById('timer'),
            fullscreenWarning: document.getElementById('fullscreen-warning'),
            countdownEl: document.getElementById('countdown'),
            submitBtn: document.getElementById('submit-btn'),
            warningEl: document.getElementById('warning'),
            examContent: document.getElementById('exam-content'),
            retryFullscreenBtn: document.getElementById('retry-fullscreen'),
            primaryCamera: document.getElementById('primary-camera'),
            secondaryCamera: document.getElementById('secondary-camera'),
            mobileStatus: document.getElementById('mobile-status'),
            audioStatus: document.getElementById('audio-status'),
            behaviorStatus: document.getElementById('behavior-status'),
            systemStatus: document.getElementById('system-status'),
            faceDetectionContainer: document.getElementById('face-detection-container'),
            qrCodeCanvas: document.getElementById('qr-code'),
            qrContainer: document.querySelector('.qr-container'),
            primaryStatus: document.getElementById('primary-status'),
            lockStatus: document.getElementById('lock-status'),
            monitorStatus: document.getElementById('monitor-status'),
            screenshotStatus: document.getElementById('screenshot-status'),
            verificationStatus: document.getElementById('verification-status')
        };

        // Initialize DOM elements safely
        function ensureElements() {
            // First, map all elements to the elements object
            elements.step1 = document.getElementById('step1');
            elements.step2 = document.getElementById('step2');
            elements.step3 = document.getElementById('step3');
            elements.step4 = document.getElementById('step4');
            elements.infoStep = document.getElementById('info-step');
            elements.idStep = document.getElementById('id-step');
            elements.faceStep = document.getElementById('face-step');
            elements.completeStep = document.getElementById('complete-step');
            elements.studentIdInput = document.getElementById('student-id');
            elements.studentNameInput = document.getElementById('student-name');
            elements.verifyInfoBtn = document.getElementById('verify-info-btn');
            elements.infoStatus = document.getElementById('info-status');
            elements.faceCamera = document.getElementById('face-camera');
            elements.captureFaceBtn = document.getElementById('capture-face');
            elements.idPreview = document.getElementById('id-preview');
            elements.idPreviewImage = document.getElementById('id-preview-image');
            elements.retakeId = document.getElementById('retake-id');
            elements.confirmId = document.getElementById('confirm-id');
            elements.idStatus = document.getElementById('id-status');
            elements.facePreview = document.getElementById('face-preview');
            elements.facePreviewImage = document.getElementById('face-preview-image');
            elements.retakeFace = document.getElementById('retake-face');
            elements.confirmFace = document.getElementById('confirm-face');
            elements.faceStatus = document.getElementById('face-status');
            elements.startExamBtn = document.getElementById('start-exam-btn');
            elements.faceDetectionPreview = document.getElementById('face-detection-preview');
            elements.welcomeScreen = document.getElementById('welcome-screen');
            elements.examScreen = document.getElementById('exam-screen');
            elements.statusBar = document.getElementById('status-bar');
            elements.violationCountEl = document.getElementById('violation-count');
            elements.timerEl = document.getElementById('timer');
            elements.fullscreenWarning = document.getElementById('fullscreen-warning');
            elements.countdownEl = document.getElementById('countdown');
            elements.submitBtn = document.getElementById('submit-btn');
            elements.warningEl = document.getElementById('warning');
            elements.examContent = document.getElementById('exam-content');
            elements.retryFullscreenBtn = document.getElementById('retry-fullscreen');
            elements.primaryCamera = document.getElementById('primary-camera');
            elements.secondaryCamera = document.getElementById('secondary-camera');
            elements.mobileStatus = document.getElementById('mobile-status');
            elements.audioStatus = document.getElementById('audio-status');
            elements.behaviorStatus = document.getElementById('behavior-status');
            elements.systemStatus = document.getElementById('system-status');
            elements.faceDetectionContainer = document.getElementById('face-detection-container');
            elements.qrCodeCanvas = document.getElementById('qr-code');
            elements.qrContainer = document.querySelector('.qr-container');
            elements.primaryStatus = document.getElementById('primary-status');
            elements.lockStatus = document.getElementById('lock-status');
            elements.monitorStatus = document.getElementById('monitor-status');
            elements.screenshotStatus = document.getElementById('screenshot-status');
            elements.verificationStatus = document.getElementById('verification-status');
            elements.idUpload = document.getElementById('id-upload');
            elements.idFile = document.getElementById('id-file');
            
            // Map the new status elements
            elements.behavioralStatus = document.getElementById('behavioral-status');
            elements.offlineStatus = document.getElementById('offline-status');
            elements.bandwidthStatus = document.getElementById('bandwidth-status');

            // Now check which elements are missing
            const requiredElements = [
                'step1', 'step2', 'step3', 'step4', 'infoStep', 'idStep', 'faceStep', 'completeStep',
                'studentIdInput', 'studentNameInput', 'verifyInfoBtn', 'infoStatus', 'faceCamera', 'captureFaceBtn', 'idPreview', 'idPreviewImage', 'retakeId', 'confirmId', 
                'idStatus', 'facePreview', 'facePreviewImage', 'retakeFace', 'confirmFace', 'faceStatus', 
                'startExamBtn', 'faceDetectionPreview', 'welcomeScreen', 'examScreen', 'statusBar', 
                'violationCountEl', 'timerEl', 'fullscreenWarning', 'countdownEl', 'submitBtn', 'warningEl', 
                'examContent', 'retryFullscreenBtn', 'primaryCamera', 'secondaryCamera', 'mobileStatus', 
                'audioStatus', 'behaviorStatus', 'systemStatus', 'faceDetectionContainer', 'qrCodeCanvas', 
                'qrContainer', 'primaryStatus', 'lockStatus', 'monitorStatus', 'screenshotStatus', 
                'verificationStatus','idUpload', 'idFile', 'behavioralStatus', 'offlineStatus', 'bandwidthStatus'
            ];
            
            for (const element of requiredElements) {
                if (!elements[element]) {
                    console.error(`Missing element: ${element}`);
                }
            }
        }



        // Event Listeners (with null checks)
        if (elements.verifyInfoBtn) elements.verifyInfoBtn.addEventListener('click', verifyStudentInfo);
        if (elements.idUpload && elements.idFile) elements.idUpload.addEventListener('click', () => elements.idFile.click());
        if (elements.idFile) elements.idFile.addEventListener('change', handleIdUpload);
        if (elements.captureFaceBtn) elements.captureFaceBtn.addEventListener('click', captureFacePhoto);
        if (elements.retakeId) elements.retakeId.addEventListener('click', retakeIdPhoto);
        if (elements.retakeFace) elements.retakeFace.addEventListener('click', retakeFacePhoto);
        if (elements.confirmId) elements.confirmId.addEventListener('click', confirmIdPhoto);
        if (elements.confirmFace) elements.confirmFace.addEventListener('click', confirmFacePhoto);
        if (elements.startExamBtn) elements.startExamBtn.addEventListener('click', startExam);
        if (elements.retryFullscreenBtn) elements.retryFullscreenBtn.addEventListener('click', attemptFullscreenRecovery);
        if (elements.submitBtn) {
            elements.submitBtn.addEventListener('click', async (e) => {
                e.preventDefault();
                
                // Confirm submission
                if (confirm('Are you sure you want to submit your exam? This action cannot be undone.')) {
                    await submitExam();
                }
            });
        }
        

        // Then call ensureElements
        ensureElements();

        // Verification functions
        function updateStepIndicator(step) {
            // Reset all steps
            elements.step1.className = 'step';
            elements.step2.className = 'step';
            elements.step3.className = 'step';
            elements.step4.className = 'step';
            
            // Mark completed steps
            for (let i = 1; i < step; i++) {
                elements[`step${i}`].className = 'step completed';
            }
            
            // Mark current step as active
            elements[`step${step}`].className = 'step active';
            
            // Show/hide steps
            elements.infoStep.style.display = step === 1 ? 'block' : 'none';
            elements.idStep.style.display = step === 2 ? 'block' : 'none';
            elements.faceStep.style.display = step === 3 ? 'block' : 'none';
            elements.completeStep.style.display = step === 4 ? 'block' : 'none';

            
        }

        

        
        function handleIdUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            const reader = new FileReader();
            reader.onload = function(e) {
                elements.idPreviewImage.src = e.target.result;
                elements.idPreview.style.display = 'block';
                elements.idUpload.style.display = 'none';
            };
            reader.readAsDataURL(file);
        }

        function retakeIdPhoto() {
            elements.idPreview.style.display = 'none';
            elements.idUpload.style.display = 'block';
            elements.idFile.value = '';
        }
        function retakeFacePhoto() {
            elements.facePreview.style.display = 'none';
            elements.captureFaceBtn.style.display = 'block';
            elements.captureFaceBtn.textContent = 'Capture Face Photo Again';
        }


        async function captureFacePhoto() {
            try {
                if (!state.faceCameraStream) {
                    await startFaceCamera();
                    // Wait a moment for the camera to initialize
                    await new Promise(resolve => setTimeout(resolve, 500));
                }
                
                // Create canvas to capture the current frame
                const canvas = document.createElement('canvas');
                canvas.width = elements.faceCamera.videoWidth;
                canvas.height = elements.faceCamera.videoHeight;
                
                // Wait for video to be ready
                if (elements.faceCamera.readyState < 2) {
                    await new Promise(resolve => {
                        elements.faceCamera.onloadeddata = resolve;
                    });
                }
                
                const ctx = canvas.getContext('2d');
                ctx.drawImage(elements.faceCamera, 0, 0, canvas.width, canvas.height);
                
                // Convert to blob and return it
                return new Promise((resolve) => {
                    canvas.toBlob((blob) => {
                        // Also display preview
                        const imageData = canvas.toDataURL('image/jpeg', 0.8);
                        elements.facePreviewImage.src = imageData;
                        elements.facePreview.style.display = 'block';
                        elements.captureFaceBtn.style.display = 'none';
                        
                        resolve(blob);
                    }, 'image/jpeg', 0.8);
                });
                
            } catch (error) {
                console.error('Error capturing face photo:', error);
                showStatus(elements.faceStatus, 'error', 'Failed to capture photo');
                throw error;
            }
        }

        // Also add the startFaceCamera function that's referenced
        async function startFaceCamera() {
            try {
                state.faceCameraStream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: 'user',
                        width: { ideal: 640 },
                        height: { ideal: 480 }
                    }
                });
                
                elements.faceCamera.srcObject = state.faceCameraStream;
                
            } catch (error) {
                console.error('Error starting face camera:', error);
                showStatus(elements.faceStatus, 'error', 'Camera access denied');
                throw error;
            }
        }

        async function verifyStudentInfo() {
            const studentId = elements.studentIdInput.value.trim();
            const studentName = elements.studentNameInput.value.trim();
            const examId = document.getElementById('exam-id') ? document.getElementById('exam-id').value.trim() : '';

            
            if (!studentId || !studentName) {
                showStatus(elements.infoStatus, 'error', 'Please enter both student ID and name');
                return;
            }
            
            try {
                elements.verifyInfoBtn.disabled = true;
                elements.verifyInfoBtn.textContent = 'Verifying...';
                showStatus(elements.infoStatus, 'pending', 'Verifying student information...');
                
                const formData = new FormData();
                formData.append('student_id', studentId);
                formData.append('student_name', studentName);
                if (examId) {
                    formData.append('exam_id', examId);
                }
                
                const response = await fetch('/api/verify-student', {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                
                if (response.ok) {
                    state.verification.studentId = result.student_id;
                    state.verification.studentName = result.student_name;
                    state.verification.status = 'info_verified';
                    state.sessionId = result.session_id;
                    
                    // Store exam data if provided
                    if (result.exam_data) {
                        state.verification.examData = result.exam_data;
                    }
                    
                    showStatus(elements.infoStatus, 'verified', 'Information verified successfully');
                    updateStepIndicator(2);
                } else {
                    showStatus(elements.infoStatus, 'failed', result.message || 'Verification failed');
                }
            } catch (error) {
                console.error('Verification error:', error);
                showStatus(elements.infoStatus, 'failed', 'Error verifying information');
            } finally {
                elements.verifyInfoBtn.disabled = false;
                elements.verifyInfoBtn.textContent = 'Verify Information';
            }
        }

        // Update confirmIdPhoto to handle file upload instead of camera capture
        async function confirmIdPhoto() {
            const file = elements.idFile.files[0];
            if (!file) {
                showStatus(elements.idStatus, 'error', 'Please select an ID photo first');
                return;
            }
            
            try {
                showStatus(elements.idStatus, 'pending', 'Uploading ID card...');
                elements.confirmId.disabled = true;
                
                const formData = new FormData();
                formData.append('file', file);
                
                const response = await fetch(`/api/sessions/${state.sessionId}/upload-id`, {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                
                if (response.ok) {
                    showStatus(elements.idStatus, 'verified', 'ID card uploaded successfully');
                    updateStepIndicator(3);
                    
                    // Start face camera for next step
                    await startFaceCamera();
                } else {
                    showStatus(elements.idStatus, 'failed', result.message || 'ID upload failed');
                }
            } catch (error) {
                console.error('ID upload error:', error);
                showStatus(elements.idStatus, 'failed', 'Error uploading ID card');
            } finally {
                elements.confirmId.disabled = false;
            }
        }

        
        // Add this function BEFORE confirmFacePhoto
        async function startExamAfterVerification() {
            try {
                const examData = {
                    exam_name: state.verification.examData.exam_name,
                    duration: state.verification.examData.exam_duration
                };
                
                const response = await fetch(`/api/sessions/${state.sessionId}/start-exam`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(examData)
                });
                
                if (response.ok) {
                    console.log('Exam started automatically after verification');
                } else {
                    console.error('Failed to start exam automatically');
                }
            } catch (error) {
                console.error('Error starting exam after verification:', error);
            }
        }


        function startExamTimer(durationMinutes) {
            if (!durationMinutes || durationMinutes <= 0) {
                console.error('Invalid exam duration:', durationMinutes);
                durationMinutes = 60; // Fallback to 60 minutes
            }
            
            let totalSeconds = durationMinutes * 60;
            state.examDuration = durationMinutes; // Store for reference
            
            // Clear any existing timer
            if (state.timerInterval) {
                clearInterval(state.timerInterval);
            }
            
            // Update timer display immediately
            updateTimerDisplay(totalSeconds);
            
            state.timerInterval = setInterval(() => {
                if (totalSeconds <= 0) {
                    clearInterval(state.timerInterval);
                    endExam(false);
                    return;
                }
                totalSeconds--;
                updateTimerDisplay(totalSeconds);
                
                // Show warnings at specific times
                if (totalSeconds === 300) { 
                    showTimeWarning("5 minutes remaining!");
                } else if (totalSeconds === 60) { 
                    showTimeWarning("1 minute remaining!", "danger");
                }
            }, 1000);
        }

        function updateTimerDisplay(totalSeconds) {
            const hours = Math.floor(totalSeconds / 3600);
            const minutes = Math.floor((totalSeconds % 3600) / 60);
            const seconds = totalSeconds % 60;

            if (elements.timerEl) {
                elements.timerEl.textContent =
                    `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            }
        }

        function showTimeWarning(message, type = "warning") {
            const warning = document.createElement('div');
            warning.style.cssText = `
                position: fixed; top: 20px; left: 50%; transform: translateX(-50%);
                background: ${type === 'danger' ? '#f8d7da' : '#fff3cd'};
                color: ${type === 'danger' ? '#721c24' : '#856404'};
                padding: 15px; border-radius: 5px; z-index: 10000;
                border-left: 4px solid ${type === 'danger' ? '#dc3545' : '#ffc107'};
                box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            `;
            warning.textContent = message;
            document.body.appendChild(warning);
            setTimeout(() => {
                if (document.body.contains(warning)) {
                    document.body.removeChild(warning);
                }
            }, 5000);
        }

        


        function showRemoteControlWarning(suspicionScore) {
            const warningDiv = document.createElement('div');
            warningDiv.style.cssText = `
                position: fixed; top: 20px; right: 20px; 
                background: #f39c12; color: white; padding: 15px; 
                border-radius: 5px; z-index: 10000; max-width: 300px;
                border-left: 4px solid #e74c3c; box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            `;
            warningDiv.innerHTML = `
                <h4 style="margin: 0 0 8px 0;">⚠️ Suspicious Activity Detected</h4>
                <p style="margin: 0;">Remote control suspicion: ${Math.round(suspicionScore * 100)}%</p>
                <p style="margin: 8px 0 0 0; font-size: 0.9em;">
                    Ensure you are not using remote assistance tools.
                </p>
            `;
            
            document.body.appendChild(warningDiv);
            
            setTimeout(() => {
                if (document.body.contains(warningDiv)) {
                    document.body.removeChild(warningDiv);
                }
            }, 10000);
        }


        async function confirmFacePhoto() {
            try {
                showStatus(elements.faceStatus, 'pending', 'Verifying face...');
                elements.confirmFace.disabled = true;

                // Capture the photo and get the blob
                const blob = await captureFacePhoto();
                if (!blob) {
                    throw new Error('No image was captured');
                }

                // Create FormData and send to server
                const formData = new FormData();
                formData.append('file', blob, 'face_photo.jpg');

                const response = await fetch(`/api/sessions/${state.sessionId}/capture-face`, {
                    method: 'POST',
                    body: formData
                });

                // ✅ First handle possible HTTP errors
                if (!response.ok) {
                    let errorMsg = `Server error: ${response.status}`;
                    try {
                        const errJson = await response.json();
                        if (errJson.message) errorMsg = errJson.message;
                    } catch (_) { /* ignore if no JSON */ }
                    throw new Error(errorMsg);
                }

                const result = await response.json();

                if (result.face_match) {
                    showStatus(elements.faceStatus, 'verified', 'Face captured and verified successfully');
                    updateStepIndicator(4);

                    if (state.verification.examData) {
                        await startExamAfterVerification();
                        state.examDuration = state.verification.examData.exam_duration;
                        elements.welcomeScreen.style.display = 'none';
                        elements.examScreen.style.display = 'block';
                        await startExam();
                    }

                    if (state.faceCameraStream) {
                        state.faceCameraStream.getTracks().forEach(track => track.stop());
                        state.faceCameraStream = null;
                    }

                } else {
                    showStatus(elements.faceStatus, 'failed', result.message || 'Face verification failed');
                }

            } catch (error) {
                console.error('Face capture error:', error);
                showStatus(elements.faceStatus, 'failed', error.message || 'Error processing face image');
            } finally {
                elements.confirmFace.disabled = false;
            }
        }



        function showStatus(element, status, message) {
            element.style.display = 'block';
            element.textContent = message;
            element.className = `verification-status ${status}`;
        }



        

        async function startExam() {
            try {
                console.log("Exam starting...");
                ensureElements();
                await requestFullscreen();

                // Exam state
                state.examStarted = true;
                state.examEnded = false;
                state.violations = 0;

                console.log("Initializing managers...");
                
                // Initialize managers
                state.offlineManager = new SecureOfflineManager(state.sessionId);
                state.bandwidthManager = new ComprehensiveBandwidthManager(state.sessionId);
                state.behavioralAnalyzer = new BehavioralAnalyzer(state.sessionId);
                state.monitorDetector = new EnhancedMonitorDetector(state.sessionId);
                state.windowMonitor = new WindowMonitor(state.sessionId);

                // Initialize managers with proper error handling
                try {
                    state.offlineManager = new SecureOfflineManager(state.sessionId);
                    await state.offlineManager.start();
                    updateStatus('offline', 'active', 'Offline mode ready');
                } catch (err) {
                    console.error('Offline manager failed:', err);
                    updateStatus('offline', 'error', 'Offline mode unavailable');
                }

                try {
                    state.bandwidthManager = new ComprehensiveBandwidthManager(state.sessionId);
                    await state.bandwidthManager.start();
                    // Status will be updated by bandwidth manager itself
                } catch (err) {
                    console.error('Bandwidth manager failed:', err);
                    updateStatus('bandwidth', 'error', 'Bandwidth monitoring failed');
                }

                try {
                    state.behavioralAnalyzer = new BehavioralAnalyzer(state.sessionId);
                    await state.behavioralAnalyzer.start();
                    updateStatus('behavioral', 'active', 'Behavioral analysis active');
                } catch (err) {
                    console.error('Behavioral analyzer failed:', err);
                    updateStatus('behavioral', 'error', 'Behavioral analysis failed');
                }


                console.log("Starting real-time monitoring...");
                startRealTimeMonitoring();

                // Start detection/monitoring
                if (state.monitorDetector.startDetection) {
                    state.monitorDetector.startDetection();
                }
                if (state.windowMonitor.startMonitoring) {
                    state.windowMonitor.startMonitoring();
                }

                // Ensure we have the exam duration
                if (!state.examDuration && state.verification.examData) {
                    state.examDuration = state.verification.examData.exam_duration;
                }

                // Fallback if still no duration
                if (!state.examDuration) {
                    state.examDuration = 60; // Default 60 minutes
                    console.warn('Using default exam duration: 60 minutes');
                }

                // Event listeners
                setupBehavioralEventListeners();
                setupEventListeners();

                // Proctoring features
                await initializeProctoring();
                generateQRCode();
                startScreenCapture();

                // Get the actual exam duration from verification data or use default
                const examDuration = state.verification.examData?.exam_duration || 
                                    state.examDuration || 
                                    60; // Default fallback

                
                console.log("Starting exam with duration:", examDuration, "minutes");
                startExamTimer(examDuration);
                setTimeout(debugMonitorDetection, 2000);

                // Connect to remote detection WebSocket
                await connectRemoteDetectionWebSocket();
                
                // Enhanced tracking
                setupEnhancedMouseTracking();
                setupNonKeyboardInputDetection();

            } catch (err) {
                console.error("Exam start error:", err);
                handleFullscreenError(err);
            }
        }


        async function submitExam() {
            try {
                const response = await fetch(`/api/sessions/${state.sessionId}/submit-exam`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    endExam(false); // Normal submission
                } else {
                    throw new Error('Failed to submit exam');
                }
            } catch (error) {
                console.error('Exam submission error:', error);
                // Fallback: end exam locally
                endExam(false);
            }
        }




        // Screen capture functions
        async function startScreenCapture() {
            try {
                updateStatus('screenshot', 'initializing', 'Starting screen capture...');
                
                // Start periodic screenshots
                const screenshotConfig = {
                    interval: config.screenshotInterval,
                    quality: 70
                };
                
                const screenshotResponse = await fetch(`/api/sessions/${state.sessionId}/start-screenshots`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(screenshotConfig)
                });
                
                if (screenshotResponse.ok) {
                    updateStatus('screenshot', 'active', 'Screen capture active');
                } else {
                    updateStatus('screenshot', 'error', 'Failed to start screen capture');
                }
                
                // Start screen recording automatically
                const recordingConfig = {
                    fps: config.recordingFPS,
                    quality: 50
                };
                
                const recordingResponse = await fetch(`/api/sessions/${state.sessionId}/start-recording`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(recordingConfig)
                });
                
                if (recordingResponse.ok) {
                    updateStatus('screenshot', 'active', 'Screen recording active');
                } else {
                    updateStatus('screenshot', 'warning', 'Screen recording not available');
                }
                
            } catch (error) {
                console.error('Screen capture error:', error);
                updateStatus('screenshot', 'error', 'Screen capture failed');
            }
        }




        // Add to your startExam function or after session creation
        async function connectRemoteDetectionWebSocket() {
            try {
                const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
                const wsUrl = `${protocol}${window.location.host}/ws/remote-detection/${state.sessionId}`;
                
                state.remoteDetectionSocket = new WebSocket(wsUrl);
                
                state.remoteDetectionSocket.onopen = () => {
                    console.log('Remote detection WebSocket connected');
                    updateStatus('system', 'active', 'Remote control detection active');
                };
                
                state.remoteDetectionSocket.onmessage = (event) => {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'suspicion_score') {
                            handleSuspicionScore(data);
                        } else if (data.type === 'violation_detected') {
                            recordViolation(data.violation_type, data.details);
                        }
                    } catch (error) {
                        console.error('Error processing remote detection message:', error);
                    }
                };
                
                state.remoteDetectionSocket.onerror = (error) => {
                    console.error('Remote detection WebSocket error:', error);
                };
                
                state.remoteDetectionSocket.onclose = () => {
                    console.log('Remote detection WebSocket closed');
                };
                
            } catch (error) {
                console.error('Failed to connect remote detection WebSocket:', error);
            }
        }

        function handleSuspicionScore(data) {
            // Update UI with suspicion score
            if (data.score > 0.7) {
                showRemoteControlWarning(data.score);
            }
            
            // Update status display
            updateStatus('behavior', 
                data.score > 0.7 ? 'warning' : 'active',
                `Suspicion: ${Math.round(data.score * 100)}% | Non-keyboard violations: ${data.non_keyboard_violations}`
            );
            
            // Check for exam termination
            if (data.exam_terminated) {
                endExam(true);
            }
        }

        // Fullscreen handling
        async function requestFullscreen() {
            try {
                const elem = document.documentElement;
                if (elem.requestFullscreen) {
                    await elem.requestFullscreen();
                } else if (elem.webkitRequestFullscreen) {
                    await elem.webkitRequestFullscreen();
                } else if (elem.msRequestFullscreen) {
                    await elem.msRequestFullscreen();
                } else {
                    throw new Error("Fullscreen API not supported");
                }
                state.isInFullscreen = true;
                state.initialResizeHandled = false;
                
                // Start periodic fullscreen checking
                if (state.fullscreenCheckInterval) {
                    clearInterval(state.fullscreenCheckInterval);
                }
                state.fullscreenCheckInterval = setInterval(handleFullscreenCheck, config.fullscreenCheckInterval);
                
            } catch (err) {
                console.error("Fullscreen error:", err);
                throw err;
            }
        }

        // Fullscreen detection
        function isReallyFullscreen() {
            return !!(
                document.fullscreenElement ||
                document.webkitFullscreenElement ||
                document.msFullscreenElement
            );
        }

        function handleFullscreenCheck() {
            const currentlyFullscreen = isReallyFullscreen();
            
            if (!currentlyFullscreen && state.examStarted && !state.examEnded) {
                if (state.isInFullscreen) {
                    recordViolation("Fullscreen exited");
                    startCountdown();
                }
            } else {
                stopCountdown();
            }
            
            state.isInFullscreen = currentlyFullscreen;
            updateLockStatus();
        }

        function updateLockStatus() {
            if (!elements.lockStatus) return;
            
            if (state.isInFullscreen) {
                elements.lockStatus.textContent = "ACTIVE";
                elements.lockStatus.style.color = "#2ecc71";
            } else {
                elements.lockStatus.textContent = "INACTIVE";
                elements.lockStatus.style.color = "#e74c3c";
            }
        }



        // UI Notification Functions
        function showRemoteToolWarning(indicator) {
            const warningDiv = document.createElement('div');
            warningDiv.className = 'remote-tool-warning';
            warningDiv.innerHTML = `
                <div style="position: fixed; top: 20px; right: 20px; background: #f8d7da; 
                        color: #721c24; padding: 15px; border-radius: 5px; z-index: 10000;
                        border-left: 4px solid #dc3545; max-width: 300px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h4 style="margin: 0 0 8px 0;">⚠️ Suspicious Activity Detected</h4>
                    <p style="margin: 0;">Pattern detected: ${indicator.replace(/_/g, ' ')}</p>
                    <p style="margin: 8px 0 0 0; font-size: 0.9em;">
                        Please ensure you are not using remote assistance tools.
                    </p>
                </div>
            `;
            
            document.body.appendChild(warningDiv);
            
            setTimeout(() => {
                if (document.body.contains(warningDiv)) {
                    document.body.removeChild(warningDiv);
                }
            }, 10000);
        }

        function showMonitorWarning(monitorCount) {
            const warningDiv = document.createElement('div');
            warningDiv.className = 'monitor-warning';
            warningDiv.innerHTML = `
                <div style="position: fixed; top: 20px; left: 50%; transform: translateX(-50%); 
                        background: #fff3cd; color: #856404; padding: 15px; border-radius: 5px; 
                        z-index: 10000; border-left: 4px solid #ffc107; max-width: 400px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center;">
                    <h4 style="margin: 0 0 8px 0;">⚠️ Multiple Displays Detected</h4>
                    <p style="margin: 0;">${monitorCount} monitors detected. Please disconnect additional displays.</p>
                </div>
            `;
            
            document.body.appendChild(warningDiv);
            
            setTimeout(() => {
                if (document.body.contains(warningDiv)) {
                    document.body.removeChild(warningDiv);
                }
            }, 15000);
        }


        // Enhanced face detection with better error handling
        async function detectFaces() {
            if (state.examEnded || !state.faceDetectionModel) {
                return;
            }

            try {
                const faces = await state.faceDetectionModel.estimateFaces({
                    input: elements.primaryCamera,
                    returnTensors: false,
                    flipHorizontal: false,
                    predictIrises: true
                });

                elements.faceDetectionContainer.innerHTML = '';

                if (!faces || faces.length === 0) {
                    updateStatus('face', 'warning', 'No face detected - ensure good lighting');
                    updateStatus('behavior', 'warning', 'No face detected for analysis');
                    setTimeout(detectFaces, config.faceDetectionInterval);
                    return;
                }

                const validFaces = faces.filter(face => 
                    face.faceInViewConfidence >= config.minFaceDetectionConfidence
                );

                if (validFaces.length > 1) {
                    updateStatus('behavior', 'warning', 'Multiple faces detected');
                    recordViolation(`Multiple faces detected (${validFaces.length})`);
                } else if (validFaces.length === 1) {
                    const face = validFaces[0];
                    try {
                        analyzeGazeDirection(face);
                        
                        // Draw face bounding box if coordinates exist
                        if (face.box && typeof face.box.xMin === 'number') {
                            const box = face.box;
                            const detectionBox = document.createElement('div');
                            detectionBox.className = 'detection-box';
                            detectionBox.style.left = `${box.xMin * 100}%`;
                            detectionBox.style.top = `${box.yMin * 100}%`;
                            detectionBox.style.width = `${(box.xMax - box.xMin) * 100}%`;
                            detectionBox.style.height = `${(box.yMax - box.yMin) * 100}%`;
                            elements.faceDetectionContainer.appendChild(detectionBox);
                        }
                        
                        updateStatus('face', 'active', `Face detected (${Math.round(face.faceInViewConfidence * 100)}% confidence)`);
                    } catch (e) {
                        console.error('Face analysis error:', e);
                        updateStatus('face', 'warning', 'Face detected but analysis failed');
                    }
                } else {
                    updateStatus('face', 'warning', 'No clear face detected - adjust position');
                }
            } catch (error) {
                console.error('Face detection error:', error);
                updateStatus('face', 'error', 'Detection error - check camera');
            }
            
            setTimeout(detectFaces, config.faceDetectionInterval);
        }



        // Enhance your existing mouse tracking
        function setupEnhancedMouseTracking() {
            let lastMouseEventTime = 0;
            let mouseEventsBuffer = [];
            
            document.addEventListener('mousemove', (e) => {
                const now = Date.now();
                const eventData = {
                    type: 'mouse_move',
                    x: e.clientX,
                    y: e.clientY,
                    screenX: e.screenX,
                    screenY: e.screenY,
                    movementX: e.movementX,
                    movementY: e.movementY,
                    timestamp: now,
                    speed: calculateMouseSpeed(e, now)
                };
                
                // Send to remote detector
                if (state.remoteDetectionSocket && state.remoteDetectionSocket.readyState === WebSocket.OPEN) {
                    state.remoteDetectionSocket.send(JSON.stringify({
                        type: 'user_interaction',
                        event_type: 'mouse_move',
                        data: eventData
                    }));
                }
                
                // Also track for behavioral analysis
                if (state.behavioralAnalyzer) {
                    state.behavioralAnalyzer.analyzeBehavior('mouse_move', eventData);
                }
                
                lastMouseEventTime = now;
            });
            
            // Track clicks
            document.addEventListener('click', (e) => {
                const eventData = {
                    type: 'click',
                    x: e.clientX,
                    y: e.clientY,
                    button: e.button,
                    timestamp: Date.now()
                };
                
                if (state.remoteDetectionSocket && state.remoteDetectionSocket.readyState === WebSocket.OPEN) {
                    state.remoteDetectionSocket.send(JSON.stringify({
                        type: 'user_interaction',
                        event_type: 'click',
                        data: eventData
                    }));
                }
            });
            
            // Track keyboard events for non-keyboard input detection
            document.addEventListener('keydown', (e) => {
                const eventData = {
                    key: e.key,
                    code: e.code,
                    timestamp: Date.now(),
                    isTrusted: e.isTrusted
                };
                
                if (state.remoteDetectionSocket && state.remoteDetectionSocket.readyState === WebSocket.OPEN) {
                    state.remoteDetectionSocket.send(JSON.stringify({
                        type: 'user_interaction',
                        event_type: 'keystroke',
                        data: eventData
                    }));
                }
                
                // Also track for behavioral analysis
                if (state.behavioralAnalyzer) {
                    state.behavioralAnalyzer.analyzeBehavior('key_press', eventData);
                }
            });
        }



        // Track paste events and other non-keyboard input
        function setupNonKeyboardInputDetection() {
            // Track paste events
            document.addEventListener('paste', (e) => {
                const pastedData = e.clipboardData.getData('text');
                
                if (state.remoteDetectionSocket && state.remoteDetectionSocket.readyState === WebSocket.OPEN) {
                    state.remoteDetectionSocket.send(JSON.stringify({
                        type: 'user_interaction',
                        event_type: 'non_keyboard_input',
                        data: {
                            input_type: 'paste',
                            data: pastedData.substring(0, 100), // Limit size
                            input_method: 'clipboard',
                            timestamp: Date.now()
                        }
                    }));
                }
                
                recordViolation("non_keyboard_input", {
                    type: "paste_event",
                    data_preview: pastedData.substring(0, 50)
                });
            });
            
            // Track drag and drop
            document.addEventListener('drop', (e) => {
                if (state.remoteDetectionSocket && state.remoteDetectionSocket.readyState === WebSocket.OPEN) {
                    state.remoteDetectionSocket.send(JSON.stringify({
                        type: 'user_interaction',
                        event_type: 'non_keyboard_input',
                        data: {
                            input_type: 'drop',
                            input_method: 'drag_drop',
                            timestamp: Date.now()
                        }
                    }));
                }
            });
        }

        function calculateMouseSpeed(e, currentTime) {
            // Calculate mouse speed based on movement and time
            if (!state.lastMousePosition) {
                state.lastMousePosition = { x: e.clientX, y: e.clientY, time: currentTime };
                return 0;
            }
            
            const deltaX = e.clientX - state.lastMousePosition.x;
            const deltaY = e.clientY - state.lastMousePosition.y;
            const deltaTime = currentTime - state.lastMousePosition.time;
            
            const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);
            const speed = deltaTime > 0 ? distance / deltaTime : 0;
            
            state.lastMousePosition = { x: e.clientX, y: e.clientY, time: currentTime };
            
            return speed;
        }

        // More robust gaze analysis
        function analyzeGazeDirection(face) {
            try {
                if (!face.annotations || !face.annotations.leftEyeIris || !face.annotations.rightEyeIris) {
                    updateStatus('behavior', 'warning', 'Cannot detect eyes');
                    return;
                }

                const leftIris = face.annotations.leftEyeIris[0];
                const rightIris = face.annotations.rightEyeIris[0];
                
                if (!leftIris || !rightIris || !face.box) {
                    updateStatus('behavior', 'warning', 'Incomplete eye data');
                    return;
                }

                const faceWidth = face.box.xMax - face.box.xMin;
                const threshold = faceWidth * 0.2;
                
                if (Math.abs(leftIris[0] - rightIris[0]) > threshold) {
                    updateStatus('behavior', 'warning', 'Looking away from screen');
                    recordViolation("Looking away from screen");
                } else {
                    updateStatus('behavior', 'active', 'Focused on screen');
                }
            } catch (error) {
                console.error('Gaze analysis error:', error);
                updateStatus('behavior', 'error', 'Gaze analysis failed');
            }
        }

        // Audio monitoring
        async function startAudioMonitoring() {
            try {
                updateStatus('audio', 'initializing', 'Setting up audio analyzer');
                
                if (!state.primaryStream) {
                    state.primaryStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                }
                
                state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
                state.analyser = state.audioContext.createAnalyser();
                state.analyser.fftSize = 256;
                
                state.mediaStreamSource = state.audioContext.createMediaStreamSource(state.primaryStream);
                state.mediaStreamSource.connect(state.analyser);
                
                updateStatus('audio', 'active', 'Audio monitoring active');
                analyzeAudio();
                
            } catch (error) {
                console.error('Audio setup error:', error);
                updateStatus('audio', 'error', `Audio error: ${error.message}`);
                recordViolation("Audio monitoring failed");
            }
        }

        // Audio analysis
        function analyzeAudio() {
            if (state.examEnded || !state.analyser) return;
            
            try {
                const bufferLength = state.analyser.frequencyBinCount;
                const dataArray = new Uint8Array(bufferLength);
                state.analyser.getByteFrequencyData(dataArray);
                
                let sum = 0;
                for (let i = 0; i < bufferLength; i++) {
                    sum += dataArray[i];
                }
                const currentLevel = sum / bufferLength;
                
                const levelChange = Math.abs(currentLevel - state.lastAudioLevel);
                state.lastAudioLevel = currentLevel;
                
                if (currentLevel > 60 && levelChange > 10) {
                    updateStatus('audio', 'warning', `Audio anomaly detected (Level: ${currentLevel.toFixed(0)})`);
                    recordViolation("Audio anomaly detected");
                } else {
                    updateStatus('audio', 'active', `Normal audio levels (Level: ${currentLevel.toFixed(0)})`);
                }
                
                setTimeout(analyzeAudio, config.audioAnalysisInterval);
                
            } catch (error) {
                console.error('Audio analysis error:', error);
                updateStatus('audio', 'error', `Analysis error: ${error.message}`);
                setTimeout(analyzeAudio, config.audioAnalysisInterval);
            }
        }

        // Enhanced mobile connection setup
        function generateQRCode() {
            try {
                updateStatus('mobile', 'initializing', 'Generating QR code');
                
                if (!state.sessionId) {
                    throw new Error("No session ID available");
                }
                
                // Use wss:// if the page is loaded over https
                const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
                const wsUrl = `${protocol}${window.location.host}/ws/mobile/${state.sessionId}`;
                
                const connectionData = {
                    sessionId: state.sessionId,
                    type: 'secondary-camera-pairing',
                    timestamp: Date.now(),
                    wsUrl: wsUrl
                };
                
                // Clear previous QR code if exists
                if (elements.qrCodeCanvas) {
                    elements.qrContainer.removeChild(elements.qrCodeCanvas);
                }
                
                // Create new canvas
                elements.qrCodeCanvas = document.createElement('canvas');
                elements.qrContainer.appendChild(elements.qrCodeCanvas);
                
                // Generate QR code with error handling
                QRCode.toCanvas(
                    elements.qrCodeCanvas,
                    JSON.stringify(connectionData),
                    {
                        width: 200,
                        margin: 2,
                        errorCorrectionLevel: 'H'
                    },
                    (error) => {
                        if (error) {
                            console.error('QR generation failed:', error);
                            updateStatus('mobile', 'error', 'QR generation failed');
                            // Retry after delay
                            setTimeout(generateQRCode, 3000);
                            return;
                        }
                        updateStatus('mobile', 'active', 'QR code generated');
                        connectToMobileSocket(wsUrl);
                    }
                );
            } catch (error) {
                console.error('QR setup error:', error);
                updateStatus('mobile', 'error', 'QR setup failed');
            }
        }

        function connectToMobileSocket(wsUrl) {
            updateStatus('mobile', 'connecting', 'Establishing connection...');

            try {
                // Close existing connection if any
                if (state.mobileSocket) {
                    state.mobileSocket.close();
                }

                state.mobileSocket = new WebSocket(wsUrl);

                state.mobileSocket.onopen = () => {
                    updateStatus('mobile', 'connected', 'Waiting for mobile...');
                    state.mobileSocket.send(JSON.stringify({
                        type: 'handshake',
                        sessionId: state.sessionId,
                        device: 'browser'
                    }));
                    
                    // Start heartbeat
                    state.mobileHeartbeat = setInterval(() => {
                        if (state.mobileSocket && state.mobileSocket.readyState === WebSocket.OPEN) {
                            state.mobileSocket.send(JSON.stringify({
                                type: 'heartbeat',
                                timestamp: Date.now()
                            }));
                        }
                    }, config.heartbeatInterval);
                };

                state.mobileSocket.onmessage = (event) => {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'mobile_stream_offer') {
                            handleMobileStreamOffer(data.offer);
                        } else if (data.type === 'heartbeat_response') {
                            updateStatus('mobile', 'connected', 'Mobile device connected');
                        } else if (data.type === 'violation') {
                            // Handle violation from mobile
                            recordViolation(data.violation.type, data.violation.details);
                        }
                    } catch (error) {
                        console.error('WebSocket message error:', error);
                    }
                };

                state.mobileSocket.onerror = (error) => {
                    console.error('WebSocket error:', error);
                    updateStatus('mobile', 'error', 'Connection error');
                };

                state.mobileSocket.onclose = () => {
                    if (state.mobileHeartbeat) clearInterval(state.mobileHeartbeat);
                    updateStatus('mobile', 'disconnected', 'Connection closed');
                    state.mobileConnected = false;
                    
                    // Attempt to reconnect after delay
                    if (state.examStarted && !state.examEnded) {
                        setTimeout(() => connectToMobileSocket(wsUrl), 5000);
                    }
                };
            } catch (error) {
                console.error('WebSocket setup error:', error);
                updateStatus('mobile', 'error', 'Connection failed');
            }
        }

        async function handleMobileStreamOffer(offer) {
            try {
                const pc = new RTCPeerConnection({
                    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
                });
                
                pc.ontrack = (event) => {
                    if (event.streams && event.streams[0]) {
                        state.secondaryStream = event.streams[0];
                        elements.secondaryCamera.srcObject = state.secondaryStream;
                        document.getElementById('mobile-pairing').style.display = 'none';
                        elements.secondaryCamera.style.display = 'block';
                        document.querySelector('#secondary-camera-feed .video-label').style.display = 'block';
                        updateStatus('mobile', 'connected', 'Mobile camera active');
                        state.mobileConnected = true;
                    }
                };
                
                await pc.setRemoteDescription(offer);
                const answer = await pc.createAnswer();
                await pc.setLocalDescription(answer);
                
                state.mobileSocket.send(JSON.stringify({
                    type: 'browser_stream_answer',
                    sessionId: state.sessionId,
                    answer: answer
                }));
                
            } catch (error) {
                console.error('Mobile stream error:', error);
                updateStatus('mobile', 'error', 'Stream setup failed');
                recordViolation("Mobile camera connection failed");
            }
        }

        // Status updates
        function updateStatus(component, status, details = '') {
            let element;
            
            switch (component) {
                case 'face':
                    element = elements.primaryStatus;
                    break;
                case 'audio':
                    element = elements.audioStatus;
                    break;
                case 'behavior':
                    element = elements.behaviorStatus;
                    break;
                case 'mobile':
                    element = elements.mobileStatus;
                    break;
                case 'system':
                    element = elements.systemStatus;
                    break;
                case 'monitor':
                    element = elements.monitorStatus;
                    break;
                case 'screenshot':
                    element = elements.screenshotStatus;
                    break;
                case 'behavioral':
                    element = elements.behavioralStatus;
                    break;
                case 'offline':
                    element = elements.offlineStatus;
                    break;
                case 'bandwidth':
                    element = elements.bandwidthStatus;
                    break;
                default:
                    return;
            }
            
            if (!element) {
                console.error(`Status element not found for component: ${component}`);
                return;
            }
            
            // Update status class
            const statusClass = `status-${status}`;
            element.className = `proctoring-status ${statusClass}`;
            
            // Update status text
            const value = element.querySelector('.status-value');
            const detailEl = element.querySelector('.status-details');
            
            if (value) {
                value.textContent = status.charAt(0).toUpperCase() + status.slice(1);
            }
            
            if (detailEl) {
                detailEl.textContent = details;
            }
        }




        function updateRealTimeStatus() {
            if (state.examEnded) return;
            
            try {
                // Update bandwidth status
                if (state.bandwidthManager && state.bandwidthManager.currentBandwidth) {
                    const bandwidth = Math.round(state.bandwidthManager.currentBandwidth);
                    const quality = state.bandwidthManager.getQualityProfile();
                    updateStatus('bandwidth', 'active', `${bandwidth} kbps (${quality} quality)`);
                }
                
                // Update behavioral analysis
                if (state.behavioralAnalyzer) {
                    const patterns = state.behavioralAnalyzer.suspiciousPatternsDetected || 0;
                    updateStatus('behavioral', patterns > 0 ? 'warning' : 'active',
                        `${patterns} suspicious patterns detected`);
                }
                
                // Update offline mode
                if (state.offlineManager) {
                    const pendingItems = state.offlineManager.pendingItems.length;
                    const status = pendingItems > 0 ? 
                        `${pendingItems} items pending sync` : 'All data synced';
                    updateStatus('offline', pendingItems > 0 ? 'warning' : 'active', status);
                }
                
                // Update other status indicators
                updateFaceDetectionRealStatus();
                updateAudioRealStatus();
                updateScreenshotRealStatus();
                
            } catch (error) {
                console.error("Error in real-time status update:", error);
            }
        }

        // Proctoring initialization with better error recovery
        async function initializeProctoring() {
            try {
                updateStatus('system', 'initializing', 'Loading components...');
                
                // Load face detection model with retry logic
                try {
                    updateStatus('face', 'initializing', 'Loading face model...');
                    state.faceDetectionModel = await faceLandmarksDetection.load(
                        faceLandmarksDetection.SupportedPackages.mediapipeFacemesh,
                        { maxFaces: 2 }
                    );
                } catch (modelError) {
                    console.error('Model loading error:', modelError);
                    updateStatus('face', 'error', 'Failed to load face model');
                    // Continue without face detection rather than failing completely
                }

                // Start camera with permission handling
                try {
                    await startPrimaryCamera();
                } catch (cameraError) {
                    console.error('Camera error:', cameraError);
                    updateStatus('face', 'error', 'Camera access denied');
                    // Continue without camera rather than failing completely
                }

                // Start audio monitoring
                try {
                    await startAudioMonitoring();
                } catch (audioError) {
                    console.error('Audio error:', audioError);
                    updateStatus('audio', 'error', 'Audio setup failed');
                    // Continue without audio
                }

                // Initialize mobile connection
                try {
                    generateQRCode();
                } catch (qrError) {
                    console.error('QR code error:', qrError);
                    updateStatus('mobile', 'error', 'QR setup failed');
                    // Continue without mobile
                }

                // Start monitor checking
                state.monitorCheckInterval = setInterval(checkMonitors, config.monitorCheckInterval);
                
                // Initial monitor check with longer delay to ensure everything is loaded
                setTimeout(checkMonitors, 5000);

                updateStatus('system', 'active', 'Proctoring active');
            } catch (error) {
                console.error('Proctoring initialization failed:', error);
                updateStatus('system', 'error', 'Initialization failed - some features may not work');
            }
        }

        // Enhance existing face detection with real counts
        async function updateFaceDetectionRealStatus() {
            if (!state.faceDetectionModel || state.examEnded) return;
            
            try {
                const faces = await state.faceDetectionModel.estimateFaces({
                    input: elements.primaryCamera,
                    returnTensors: false
                });
                
                const faceCount = faces ? faces.length : 0;
                const status = faceCount > 0 ? 
                    `${faceCount} face(s) detected` : 'No faces detected';
                
                updateStatus('face', 
                    faceCount > 0 ? 'active' : 'warning',
                    status);
            } catch (error) {
                console.error('Face detection status error:', error);
                updateStatus('face', 'error', 'Detection error');
            }
        }

        




        // Enhance existing audio monitoring with real levels
        function updateAudioRealStatus() {
            if (!state.analyser || state.examEnded) return;
            
            try {
                const bufferLength = state.analyser.frequencyBinCount;
                const dataArray = new Uint8Array(bufferLength);
                state.analyser.getByteFrequencyData(dataArray);
                
                let sum = 0;
                for (let i = 0; i < bufferLength; i++) {
                    sum += dataArray[i];
                }
                const avgLevel = Math.round(sum / bufferLength);
                
                updateStatus('audio', 'active', `Audio level: ${avgLevel}/255`);
            } catch (error) {
                console.error('Audio status error:', error);
                updateStatus('audio', 'error', 'Audio monitoring error');
            }
        }

        

        // Use existing screenshot infrastructure for real counts
        async function updateScreenshotRealStatus() {
            try {
                // Use a simple counter for now - you can enhance this with actual API calls
                const screenshotCount = state.screenshotsTaken || 0;
                updateStatus('screenshot', 'active', `${screenshotCount} screenshots captured`);
            } catch (error) {
                console.error('Screenshot status error:', error);
                updateStatus('screenshot', 'error', 'Capture status unavailable');
            }
        }



        // Replace the existing startRealTimeMonitoring function with this:
        function startRealTimeMonitoring() {
            console.log("Starting real-time monitoring...");
            
            // Initial delay to let systems initialize
            setTimeout(() => {
                // Update all status indicators immediately
                updateRealTimeStatus();
                
                // Set up periodic updates every 3 seconds
                state.realTimeInterval = setInterval(updateRealTimeStatus, 3000);
            }, 2000);
            
            // Also update on various events
            if (state.behavioralAnalyzer) {
                // Store original method
                const originalAnalyze = state.behavioralAnalyzer.analyzeBehavior;
                
                // Override to trigger UI updates
                state.behavioralAnalyzer.analyzeBehavior = function(...args) {
                    const result = originalAnalyze.apply(this, args);
                    // Schedule UI update after analysis
                    setTimeout(updateRealTimeStatus, 100);
                    return result;
                };
            }
        }

        // Also make sure to clear the interval when exam ends
        function stopRealTimeMonitoring() {
            if (state.realTimeInterval) {
                clearInterval(state.realTimeInterval);
                state.realTimeInterval = null;
            }
        }



        async function startPrimaryCamera() {
            try {
                updateStatus('face', 'initializing', 'Requesting camera access');
                
                // Get camera stream
                state.primaryStream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: 'user',
                        width: { ideal: 1280 },
                        height: { ideal: 720 }
                    },
                    audio: true
                });
                
                elements.primaryCamera.srcObject = state.primaryStream;
                
                // WAIT FOR VIDEO TO LOAD BEFORE STARTING DETECTION
                await new Promise((resolve) => {
                    elements.primaryCamera.onloadeddata = resolve;
                });
                
                updateStatus('face', 'active', 'Camera active, starting detection');
                
                // Start face detection loop
                detectFaces();
                
            } catch (error) {
                console.error('Camera error:', error);
                updateStatus('face', 'error', `Camera access error: ${error.message}`);
                recordViolation("Camera access denied");
            }
        }

        let lastViolationTime = 0;
        // Violation handling
        async function recordViolation(type, details = {}) {
            
            const now = Date.now();
            if (now - lastViolationTime < 1000) return;  // Skip if <1s since last violation
            lastViolationTime = now;
            
            if (!state.sessionId || state.examEnded) return;  // Skip if exam ended
            
            try {
                // 1. Send violation to backend
                const response = await fetch(`/api/sessions/${state.sessionId}/violations`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type, details })
                });
                
                // 2. Update UI with backend's count
                const result = await response.json();
                state.violations = result.total_violations;
                elements.violationCountEl.textContent = state.violations;
                
                // 3. End exam ONLY at exactly 5 violations
                if (state.violations === 5) {
                    endExam(true);
                }
            } catch (err) {
                console.error("Failed to record violation:", err);
            }
        }
        // Timer functions
        function startTimer() {
            state.timerInterval = setInterval(() => {
                state.examDuration++;
                const hours = Math.floor(state.examDuration / 3600);
                const minutes = Math.floor((state.examDuration % 3600) / 60);
                const seconds = state.examDuration % 60;
    
                if (elements.timerEl) {
                    elements.timerEl.textContent = 
                        `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
                }
            }, 1000);
        }

        // Exam control functions
        function endExam(forced = false) {
            state.examEnded = true;

            stopRealTimeMonitoring();
            clearInterval(state.timerInterval);
            clearInterval(state.countdownInterval);
            clearInterval(state.fullscreenCheckInterval);
            clearInterval(state.monitorCheckInterval);
            if (elements.fullscreenWarning) {
                elements.fullscreenWarning.style.display = 'none';
            }

            stopProctoring();

            // Stop screen capture
            fetch(`/api/sessions/${state.sessionId}/stop-screenshots`, {
                method: 'POST'
            }).catch(err => console.error('Error stopping screenshots:', err));
            
            fetch(`/api/sessions/${state.sessionId}/stop-recording`, {
                method: 'POST'
            }).catch(err => console.error('Error stopping recording:', err));

            if (document.exitFullscreen) document.exitFullscreen();
            else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
            else if (document.msExitFullscreen) document.msExitFullscreen();

            document.body.innerHTML = `
                <div style="text-align: center; padding: 40px; font-family: sans-serif;">
                    <h1>${forced ? '⚠️ Exam Terminated' : '✅ Exam Submitted'}</h1>
                    <p><strong>Duration:</strong> ${formatTime(state.examDuration)}</p>
                    <p><strong>Violations:</strong> ${state.violations}</p>
                    <p><strong>Status:</strong> ${forced ? "Auto-submitted due to policy violations." : "Submitted successfully."}</p>
                </div>
            `;
        }

        function formatTime(seconds) {
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const secs = seconds % 60;
            return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        }

        function stopProctoring() {
            if (state.primaryStream) {
                state.primaryStream.getTracks().forEach(track => track.stop());
            }
            if (state.secondaryStream) {
                state.secondaryStream.getTracks().forEach(track => track.stop());
            }
            
            if (state.mobileSocket) {
                state.mobileSocket.close();
            }
            
            if (state.mobileHeartbeat) {
                clearInterval(state.mobileHeartbeat);
            }
            
            if (state.monitorCheckInterval) {
                clearInterval(state.monitorCheckInterval);
            }
            
            if (state.audioContext && state.audioContext.state !== 'closed') {
                state.audioContext.close();
            }
        }

        // Fullscreen countdown
        function startCountdown() {
            if (state.countdownInterval || state.examEnded) return;
    
            if (elements.fullscreenWarning) {
                elements.fullscreenWarning.style.display = 'flex';
            }
            state.countdown = 5;
            if (elements.countdownEl) {
                elements.countdownEl.textContent = state.countdown;
            }
    
            state.countdownInterval = setInterval(() => {
                state.countdown--;
                if (elements.countdownEl) {
                    elements.countdownEl.textContent = state.countdown;
                }
    
                if (state.countdown <= 0) {
                    clearInterval(state.countdownInterval);
                    state.countdownInterval = null;
                    endExam(true);
                }
            }, 1000);
        }

        function stopCountdown() {
            if (state.countdownInterval) {
                clearInterval(state.countdownInterval);
                state.countdownInterval = null;
            }
            if (elements.fullscreenWarning) {
                elements.fullscreenWarning.style.display = 'none';
            }
        }

        async function attemptFullscreenRecovery() {
            if (state.examEnded) return;
    
            try {
                await requestFullscreen();
                stopCountdown();
            } catch (err) {
                alert("Couldn't return to fullscreen: " + err.message);
            }
        }

        function handleFullscreenError(err) {
            alert("Fullscreen is required to continue. Please allow fullscreen access.");
            if (elements.welcomeScreen) elements.welcomeScreen.style.display = 'block';
            if (elements.examScreen) elements.examScreen.style.display = 'none';
            state.examStarted = false;
    
            clearInterval(state.timerInterval);
            clearInterval(state.countdownInterval);
            clearInterval(state.fullscreenCheckInterval);
            clearInterval(state.monitorCheckInterval);
        }


        




        // Monitor detection functions - REVISED
        async function checkMonitors() {
            try {
                // Don't check again if we've already found a violation
                if (state.monitorViolationDetected) {
                    return;
                }
                
                updateStatus('monitor', 'active', 'Checking display configuration...');
                
                let monitorCount = 1;
                let detectionMethod = "default";
                let confidence = "low";
                
                // Method 1: Try Screen Details API (modern browsers) - Most reliable
                if ('getScreenDetails' in window) {
                    try {
                        const screenDetails = await window.getScreenDetails();
                        monitorCount = screenDetails.screens.length;
                        detectionMethod = "Screen Details API";
                        confidence = "high";
                        console.log(`Monitor detection: ${monitorCount} monitors detected via ${detectionMethod}`);
                    } catch (error) {
                        console.log('Screen Details API not available or permission denied:', error);
                    }
                }
                
                // Method 2: Check screen extension property - Moderately reliable
                if (monitorCount === 1 && window.screen && window.screen.isExtended) {
                    // This property is fairly reliable for detecting multiple monitors
                    monitorCount = 2;
                    detectionMethod = "Screen.isExtended";
                    confidence = "medium";
                    console.log(`Monitor detection: Multiple monitors detected via ${detectionMethod}`);
                }
                
                // Method 3: Use more conservative heuristic checks
                if (monitorCount === 1) {
                    const heuristicResult = conservativeHeuristicCheck();
                    if (heuristicResult.count > 1 && heuristicResult.confidence === "high") {
                        monitorCount = heuristicResult.count;
                        detectionMethod = "Heuristic Check";
                        confidence = heuristicResult.confidence;
                        console.log(`Monitor detection: ${monitorCount} monitors detected via ${detectionMethod}`);
                    }
                }
                
                // Only trigger violation for high-confidence detections
                if (monitorCount > 1) {
                    if (confidence === "high" || 
                        (confidence === "medium" && confirmMultipleMonitors())) {
                        
                        // Set flag to prevent further checking
                        state.monitorViolationDetected = true;
                        
                        updateStatus('monitor', 'danger', `Multiple monitors detected (${monitorCount})`);
                        recordViolation("multiple_monitors", { 
                            monitor_count: monitorCount,
                            detection_method: detectionMethod,
                            confidence: confidence
                        });
                        
                        // Report to backend
                        try {
                            await fetch(`/api/sessions/${state.sessionId}/monitor-check?monitor_count=${monitorCount}`, {
                                method: 'GET'
                            });
                        } catch (error) {
                            console.error('Failed to report monitor violation to server:', error);
                        }
                        
                        // Stop further monitoring checks once violation is detected
                        if (state.monitorCheckInterval) {
                            clearInterval(state.monitorCheckInterval);
                            console.log("Stopped monitor checking due to violation");
                        }
                    } else {
                        // Medium confidence but not confirmed - log but don't record violation
                        console.log(`Suspected multiple monitors (${monitorCount}) but not confirmed`);
                        updateStatus('monitor', 'active', 'Single monitor detected');
                    }
                } else {
                    updateStatus('monitor', 'active', 'Single monitor detected');
                }
                
            } catch (error) {
                console.error('Monitor detection error:', error);
                updateStatus('monitor', 'error', 'Detection temporarily unavailable');
            }
        }


        // Remote Tool Monitoring
        async function monitorRemoteTools() {
            if (!state.sessionId || state.examEnded) return;

            try {
                const response = await fetch(`/api/sessions/${state.sessionId}/check-remote-tools`);
                const result = await response.json();
                
                if (result.detected_indicators && result.detected_indicators.length > 0) {
                    showRemoteToolWarning(result.detected_indicators[0]);
                }
                
                setTimeout(monitorRemoteTools, 30000);
            } catch (error) {
                setTimeout(monitorRemoteTools, 60000);
            }
        }


        // More conservative heuristic check
        function conservativeHeuristicCheck() {
            try {
                const screenWidth = window.screen.width;
                const screenHeight = window.screen.height;
                const screenAvailWidth = window.screen.availWidth;
                const screenAvailLeft = window.screen.availLeft;
                const screenAvailTop = window.screen.availTop;
                
                const windowLeft = window.screenLeft || window.screenX;
                const windowTop = window.screenTop || window.screenY;
                
                let confidence = "low";
                let count = 1;
                
                // High confidence check: Window clearly outside screen bounds
                if (windowLeft > screenWidth + 50 || windowLeft < -50 || 
                    windowTop > screenHeight + 50 || windowTop < -50) {
                    count = 2;
                    confidence = "high";
                }
                // High confidence: Window spans significantly beyond screen
                else if (window.outerWidth > screenWidth + 100) {
                    count = 2;
                    confidence = "high";
                }
                // Medium confidence: Significant difference in available width
                else if (Math.abs(screenWidth - screenAvailWidth) > 300) {
                    count = 2;
                    confidence = "medium";
                }
                
                return { count, confidence };
            } catch (error) {
                console.warn('Heuristic monitor check failed:', error);
                return { count: 1, confidence: "low" };
            }
        }




        // Additional confirmation for medium confidence detections
        function confirmMultipleMonitors() {
            // Check multiple factors to reduce false positives
            const checks = [];
            
            // Check 1: Window position relative to screen
            const windowLeft = window.screenLeft || window.screenX;
            const windowTop = window.screenTop || window.screenY;
            checks.push(windowLeft < 0 || windowTop < 0);
            
            // Check 2: Screen properties
            checks.push(window.screen.availLeft !== 0);
            checks.push(window.screen.availTop !== 0);
            
            // Check 3: Window size vs screen size
            checks.push(window.outerWidth > window.screen.width * 0.9);
            
            // Require at least 2 positive checks to confirm
            const positiveChecks = checks.filter(Boolean).length;
            return positiveChecks >= 2;
        }

        // Add this function to help debug monitor detection
        function debugMonitorDetection() {
            console.log('=== MONITOR DETECTION DEBUG INFO ===');
            console.log('Screen width:', window.screen.width);
            console.log('Screen height:', window.screen.height);
            console.log('Available width:', window.screen.availWidth);
            console.log('Available height:', window.screen.availHeight);
            console.log('Available left:', window.screen.availLeft);
            console.log('Available top:', window.screen.availTop);
            console.log('Window position - X:', window.screenX, 'Y:', window.screenY);
            console.log('Window position - Left:', window.screenLeft, 'Top:', window.screenTop);
            console.log('Window outer size - Width:', window.outerWidth, 'Height:', window.outerHeight);
            console.log('Screen.isExtended:', window.screen.isExtended);
            console.log('Screen Details API available:', 'getScreenDetails' in window);
            console.log('====================================');
        }



        function setupBehavioralEventListeners() {
            // Mouse movement tracking
            document.addEventListener('mousemove', (e) => {
                if (state.behavioralAnalyzer && state.examStarted && !state.examEnded) {
                    state.behavioralAnalyzer.analyzeBehavior('mouse_move', {
                        x: e.clientX,
                        y: e.clientY,
                        timestamp: Date.now()
                    });
                }
            });
            
            // Keystroke timing
            document.addEventListener('keydown', (e) => {
                if (state.behavioralAnalyzer && state.examStarted && !state.examEnded) {
                    state.behavioralAnalyzer.analyzeBehavior('key_press', {
                        key: e.key,
                        timestamp: Date.now(),
                        timing: performance.now()
                    });
                }
            });
            
            // Focus changes
            window.addEventListener('blur', () => {
                if (state.behavioralAnalyzer && state.examStarted && !state.examEnded) {
                    state.behavioralAnalyzer.analyzeBehavior('focus_change', {
                        type: 'window_blur',
                        timestamp: Date.now()
                    });
                }
            });
            
            window.addEventListener('focus', () => {
                if (state.behavioralAnalyzer && state.examStarted && !state.examEnded) {
                    state.behavioralAnalyzer.analyzeBehavior('focus_change', {
                        type: 'window_focus',
                        timestamp: Date.now()
                    });
                }
            });
        }



        // Event listeners setup
        function setupEventListeners() {
            // Fullscreen detection
            document.addEventListener('fullscreenchange', handleFullscreenCheck);
            document.addEventListener('webkitfullscreenchange', handleFullscreenCheck);
            document.addEventListener('MSFullscreenChange', handleFullscreenCheck);
            
            // Page visibility API
            document.addEventListener('visibilitychange', () => {
                if (document.hidden && state.examStarted && !state.examEnded) {
                    recordViolation("Tab/window switched");
                    startCountdown();
                }
            });
            
            // Window focus/blur events
            window.addEventListener('blur', () => {
                if (state.examStarted && !state.examEnded && !document.hidden) {
                    recordViolation("Window focus lost");
                    startCountdown();
                }
            });
            
            window.addEventListener('focus', () => {
                if (state.examStarted && !state.examEnded) {
                    handleFullscreenCheck();
                }
            });
            
            // Mouse leaving window
            document.addEventListener('mouseleave', (e) => {
                if (e.clientY <= 0 && state.examStarted && !state.examEnded) {
                    recordViolation("Mouse left window");
                    startCountdown();
                }
            });
            
            // Disable right-click
            document.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                recordViolation("Right-click attempted");
            });
            
            // Disable keyboard shortcuts
            document.addEventListener('keydown', (e) => {
                if ((e.ctrlKey || e.metaKey) || 
                    (e.key >= 'F1' && e.key <= 'F12') || 
                    e.key === 'F11' || 
                    e.key === 'Escape') {
                    e.preventDefault();
                    recordViolation(`Blocked ${e.key} key`);
                }
            });
            
            // Periodic fullscreen check
            state.fullscreenCheckInterval = setInterval(() => {
                handleFullscreenCheck();
                // Additional check for developer tools
                if (state.examStarted && !state.examEnded) {
                    const widthThreshold = window.outerWidth - window.innerWidth > 160;
                    const heightThreshold = window.outerHeight - window.innerHeight > 160;
                    if (widthThreshold || heightThreshold) {
                        recordViolation("Potential developer tools open");
                    }
                }
            }, config.fullscreenCheckInterval);
        }




        

        // Initialize critical elements check
        ensureElements();
        updateStepIndicator(1);



        class SecureOfflineManager {
            constructor(sessionId) {
                this.sessionId = sessionId;
                this.pendingItems = [];
                this.encryptionKey = this.generateEncryptionKey();
                this.isOnline = navigator.onLine;
        
            }


            // Add this start method
            async start() {
                console.log("Offline manager started");
                // Initialize offline storage and start periodic sync checks
                this.startSyncChecks();
                return true;
            }
            
            startSyncChecks() {
                // Check for connectivity and sync every 30 seconds
                setInterval(() => {
                    this.checkConnectivityAndSync();
                }, 10000);
            }
            
            async checkConnectivityAndSync() {
                this.isOnline = navigator.onLine;
                if (this.isOnline && this.pendingItems.length > 0) {
                    await this.syncWhenOnline();
                }
            }
            
            
            generateEncryptionKey() {
                // Generate a crypto key for encryption
                // In production, use proper key derivation
                return crypto.getRandomValues(new Uint8Array(32));
            }
            
            async encryptData(data) {
                // Simple encryption implementation
                // In production, use Web Crypto API properly
                const dataStr = JSON.stringify(data);
                const encoder = new TextEncoder();
                const dataBytes = encoder.encode(dataStr);
                
                // Simple XOR "encryption" for demo purposes
                // REPLACE WITH PROPER ENCRYPTION IN PRODUCTION
                const encryptedBytes = new Uint8Array(dataBytes.length);
                for (let i = 0; i < dataBytes.length; i++) {
                    encryptedBytes[i] = dataBytes[i] ^ this.encryptionKey[i % this.encryptionKey.length];
                }
                
                return btoa(String.fromCharCode(...encryptedBytes));
            }
            
            async decryptData(encryptedData) {
                // Decrypt data
                const encryptedBytes = new Uint8Array(
                    atob(encryptedData).split('').map(c => c.charCodeAt(0))
                );
                
                const decryptedBytes = new Uint8Array(encryptedBytes.length);
                for (let i = 0; i < encryptedBytes.length; i++) {
                    decryptedBytes[i] = encryptedBytes[i] ^ this.encryptionKey[i % this.encryptionKey.length];
                }
                
                const decoder = new TextDecoder();
                return JSON.parse(decoder.decode(decryptedBytes));
            }
            
            async storeItem(type, data) {
                const item = {
                    id: Date.now() + '-' + Math.random().toString(36).substr(2, 9),
                    type: type,
                    timestamp: new Date().toISOString(),
                    session_id: this.sessionId
                };
                
                // Encrypt the data
                item.encrypted_data = await this.encryptData({
                    type: type,
                    data: data,
                    timestamp: item.timestamp
                });
                
                this.pendingItems.push(item);
                this.saveToStorage();
                
                return item.id;
            }
            
            async syncWhenOnline() {
                if (this.pendingItems.length === 0 || !this.isOnline) return;
                
                try {
                    const response = await fetch(`/api/sessions/${this.sessionId}/sync-offline-data`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(this.pendingItems)
                    });
                    
                    if (response.ok) {
                        const result = await response.json();
                        
                        // Remove successfully synced items
                        this.pendingItems = this.pendingItems.filter(item => 
                            !result.results.some(r => r.id === item.id && r.status === 'synced')
                        );
                        
                        this.saveToStorage();
                        
                        if (result.synced_items > 0) {
                            this.showSyncNotification(`Synced ${result.synced_items} offline items`);
                        }
                    }
                } catch (error) {
                    console.error('Offline sync failed:', error);
                }
            }
            

        }


        


        // Add this JavaScript class to your HTML template
        class BehavioralAnalyzer {
            constructor(sessionId) {
                this.sessionId = sessionId;
                this.suspiciousPatternsDetected = 0;
                this.mouseMovements = [];
                this.keystrokeTimings = [];
                this.focusChanges = [];
                this.lastAnalysisTime = new Date();
            }

                
            // Add this start method
            async start() {
                console.log("Behavioral analyzer started");
                // Start the periodic analysis
                this.startPeriodicAnalysis();
                return true;
            }
                
            startPeriodicAnalysis() {
                // Perform analysis every 30 seconds
                setInterval(() => {
                    this.performBehavioralAnalysis();
                }, 10000);
            }

            async analyzeBehavior(eventType, eventData) {
                if (eventType === 'mouse_move') {
                    this.mouseMovements.push(eventData);
                } else if (eventType === 'key_press') {
                    this.keystrokeTimings.push(eventData);
                } else if (eventType === 'focus_change') {
                    this.focusChanges.push(eventData);
                }

                // Analyze periodically (every 30 seconds)
                if ((new Date() - this.lastAnalysisTime) > 10000) {
                    await this.performBehavioralAnalysis();
                    this.lastAnalysisTime = new Date();
                }
            }

            
                
            async performBehavioralAnalysis() {
                const suspiciousPatterns = [];
                
                // Analyze mouse movements for automation patterns
                if (this.detectAutomatedMouseMovements()) {
                    suspiciousPatterns.push('automated_mouse_movements');
                }
                
                // Analyze keystroke timing for consistency
                if (this.detectConsistentKeystrokeTiming()) {
                    suspiciousPatterns.push('consistent_keystroke_timing');
                }

                // Analyze focus changes for rapid switching
                if (this.detectRapidFocusChanges()) {
                    suspiciousPatterns.push('rapid_focus_changes');
                }

                if (suspiciousPatterns.length > 0) {
                    this.suspiciousPatternsDetected += suspiciousPatterns.length;

                    // Record violation by calling the server API
                    try {
                        const response = await fetch(`/api/sessions/${this.sessionId}/violations`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                type: "suspicious_behavior_patterns",
                                details: {
                                    patterns: suspiciousPatterns,
                                    mouse_movements: this.mouseMovements.length,
                                    keystrokes: this.keystrokeTimings.length,
                                    focus_changes: this.focusChanges.length,
                                    timestamp: new Date().toISOString()
                                }
                            })
                        });
                        
                        if (response.ok) {
                            const result = await response.json();
                            // Update violation count if needed
                            if (result.total_violations !== undefined) {
                                state.violations = result.total_violations;
                                if (elements.violationCountEl) {
                                    elements.violationCountEl.textContent = state.violations;
                                }
                            }
                        }
                    } catch (error) {
                        console.error('Failed to record behavioral violation:', error);
                    }
                }
                
                // Clear stored data for next analysis period
                this.mouseMovements = [];
                this.keystrokeTimings = [];
                this.focusChanges = [];
            }


            detectAutomatedMouseMovements() {
                if (this.mouseMovements.length < 10) {
                    return false;
                }
                
                // Check for perfectly straight lines (simplified)
                let straightLineCount = 0;
                for (let i = 0; i < this.mouseMovements.length - 1; i++) {
                    const movement = this.mouseMovements[i];
                    const nextMovement = this.mouseMovements[i + 1];
                    
                    // Simple straightness check
                    const dx = Math.abs(nextMovement.x - movement.x);
                    const dy = Math.abs(nextMovement.y - movement.y);
                    
                    if (dx > 0 && dy > 0 && Math.abs(dx - dy) / Math.max(dx, dy) < 0.1) {
                        straightLineCount++;
                    }
                }
                
                // If more than 30% of movements are perfectly straight
                return straightLineCount / this.mouseMovements.length > 0.3;
            }

            detectConsistentKeystrokeTiming() {
                if (this.keystrokeTimings.length < 20) {
                    return false;
                }
                
                // Calculate timing variance
                const timings = this.keystrokeTimings.map(k => k.timing);
                const mean = timings.reduce((a, b) => a + b, 0) / timings.length;
                const variance = timings.reduce((sum, t) => sum + Math.pow(t - mean, 2), 0) / timings.length;
                
                // Very low variance suggests automation
                return variance < 10; // milliseconds
            }

            detectRapidFocusChanges() {
                return this.focusChanges.length > 15; // More than 15 changes in 30 seconds
            }

            calculateMovementCharacteristics(current, next) {
                // Calculate movement characteristics for more advanced analysis
                const dx = next.x - current.x;
                const dy = next.y - current.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                const angle = Math.atan2(dy, dx);
                const straightness = 1; // Placeholder for actual calculation
                
                return {
                    distance,
                    angle,
                    straightness
                };
            }

            calculateVariance(values) {
                const mean = values.reduce((a, b) => a + b, 0) / values.length;
                return values.reduce((sum, value) => sum + Math.pow(value - mean, 2), 0) / values.length;
            }
        }


        class EnhancedMonitorDetector {
            constructor(sessionId) {
                this.sessionId = sessionId;
                this.detectionInterval = null;
                this.lastMonitorCount = 1;
                this.violationReported = false;
            }

            startDetection() {
                // Start periodic monitor detection
                this.detectionInterval = setInterval(() => {
                    this.detectMonitors();
                }, 10000); // Check every 10 seconds

                // Initial detection
                setTimeout(() => this.detectMonitors(), 2000);
            }

            async detectMonitors() {
                try {
                    let monitorCount = 1;
                    let detectionMethod = "default";
                    let confidence = "low";

                    // Method 1: Try Screen Details API (modern browsers)
                    if ('getScreenDetails' in window) {
                        try {
                            const screenDetails = await window.getScreenDetails();
                            monitorCount = screenDetails.screens.length;
                            detectionMethod = "Screen Details API";
                            confidence = "high";
                        } catch (error) {
                            console.log('Screen Details API not available:', error);
                        }
                    }

                    // Method 2: Check screen extension property
                    if (monitorCount === 1 && window.screen && window.screen.isExtended) {
                        monitorCount = 2;
                        detectionMethod = "Screen.isExtended";
                        confidence = "medium";
                    }

                    // Method 3: Use heuristic checks
                    if (monitorCount === 1) {
                        const heuristicResult = this.conservativeHeuristicCheck();
                        if (heuristicResult.count > 1 && heuristicResult.confidence === "high") {
                            monitorCount = heuristicResult.count;
                            detectionMethod = "Heuristic Check";
                            confidence = heuristicResult.confidence;
                        }
                    }

                    // Only report if monitor count changed and we have high confidence
                    if (monitorCount > this.lastMonitorCount && (confidence === "high" || 
                        (confidence === "medium" && this.confirmMultipleMonitors()))) {
                        
                        this.reportMultipleMonitors(monitorCount, detectionMethod, confidence);
                    }

                    this.lastMonitorCount = monitorCount;

                } catch (error) {
                    console.error('Monitor detection error:', error);
                }
            }

            conservativeHeuristicCheck() {
                try {
                    const screenWidth = window.screen.width;
                    const screenHeight = window.screen.height;
                    const screenAvailWidth = window.screen.availWidth;
                    
                    const windowLeft = window.screenLeft || window.screenX;
                    const windowTop = window.screenTop || window.screenY;
                    
                    let confidence = "low";
                    let count = 1;
                    
                    // High confidence check: Window clearly outside screen bounds
                    if (windowLeft > screenWidth + 50 || windowLeft < -50 || 
                        windowTop > screenHeight + 50 || windowTop < -50) {
                        count = 2;
                        confidence = "high";
                    }
                    // High confidence: Window spans significantly beyond screen
                    else if (window.outerWidth > screenWidth + 100) {
                        count = 2;
                        confidence = "high";
                    }
                    // Medium confidence: Significant difference in available width
                    else if (Math.abs(screenWidth - screenAvailWidth) > 300) {
                        count = 2;
                        confidence = "medium";
                    }
                    
                    return { count, confidence };
                } catch (error) {
                    console.warn('Heuristic monitor check failed:', error);
                    return { count: 1, confidence: "low" };
                }
            }

            confirmMultipleMonitors() {
                // Check multiple factors to reduce false positives
                const checks = [];
                
                // Check 1: Window position relative to screen
                const windowLeft = window.screenLeft || window.screenX;
                const windowTop = window.screenTop || window.screenY;
                checks.push(windowLeft < 0 || windowTop < 0);
                
                // Check 2: Screen properties
                checks.push(window.screen.availLeft !== 0);
                checks.push(window.screen.availTop !== 0);
                
                // Check 3: Window size vs screen size
                checks.push(window.outerWidth > window.screen.width * 0.9);
                
                // Require at least 2 positive checks to confirm
                const positiveChecks = checks.filter(Boolean).length;
                return positiveChecks >= 2;
            }

            async reportMultipleMonitors(count, method, confidence) {
                if (this.violationReported) return;
                
                try {
                    // Report to server
                    const response = await fetch(`/api/sessions/${this.sessionId}/monitor-check?monitor_count=${count}`, {
                        method: 'GET'
                    });
                    
                    if (response.ok) {
                        this.violationReported = true;
                        console.log(`Multiple monitors detected: ${count} (via ${method}, ${confidence} confidence)`);
                        
                        // Also record as violation
                        recordViolation("multiple_monitors", {
                            monitor_count: count,
                            detection_method: method,
                            confidence: confidence
                        });
                    }
                } catch (error) {
                    console.error('Failed to report monitor violation:', error);
                }
            }

            stopDetection() {
                if (this.detectionInterval) {
                    clearInterval(this.detectionInterval);
                    this.detectionInterval = null;
                }
            }
        }



        class WindowMonitor {
            constructor(sessionId) {
                this.sessionId = sessionId;
                this.previousWindows = [];
                this.monitoringInterval = null;
            }
            
            startMonitoring() {
                this.monitoringInterval = setInterval(() => {
                    this.analyzeWindows();
                }, 10000); // Check every 10 seconds
            }
            
            async analyzeWindows() {
                try {
                    // Get window information (simplified - would use browser extension in production)
                    const windowInfo = {
                        timestamp: Date.now(),
                        windows: await this.getWindowData(),
                        focusChanges: this.trackFocusChanges()
                    };
                    
                    // Send to backend for analysis
                    if (windowInfo.windows.length > 0) {
                        fetch(`/api/sessions/${this.sessionId}/window-analysis`, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(windowInfo)
                        });
                    }
                    
                } catch (error) {
                    console.error('Window analysis failed:', error);
                }
            }
            
            async getWindowData() {
                // This would be implemented with a browser extension
                // For now, return basic window data
                return [{
                    id: 'main',
                    title: document.title,
                    width: window.outerWidth,
                    height: window.outerHeight,
                    x: window.screenX,
                    y: window.screenY,
                    alwaysOnTop: false, // Would require extension
                    focused: document.hasFocus()
                }];
            }
            
            trackFocusChanges() {
                // Track how often focus changes
                let changes = 0;
                
                window.addEventListener('focus', () => {
                    changes++;
                });
                
                return changes;
            }
        }

        
        class PreciseMouseTracker {
            constructor(sessionId) {
                this.sessionId = sessionId;
                this.mouseEvents = [];
                this.lastMouseEvent = null;
                this.startTime = performance.now();
            }
            
            startTracking() {
                // Capture ALL mouse events with high precision
                const eventTypes = ['mousemove', 'mousedown', 'mouseup', 'click', 'mouseenter', 'mouseleave'];
                
                eventTypes.forEach(eventType => {
                    document.addEventListener(eventType, (e) => {
                        this.captureMouseEvent(eventType, e);
                    }, { capture: true }); // Use capture phase to catch all events
                });
                
                // Also track mouse wheel events
                document.addEventListener('wheel', (e) => {
                    this.captureMouseEvent('wheel', e);
                });
            }
            
            captureMouseEvent(type, event) {
                const mouseEvent = {
                    timestamp: performance.now(),
                    type: type,
                    x: event.clientX,
                    y: event.clientY,
                    screenX: event.screenX,
                    screenY: event.screenY,
                    movementX: event.movementX || 0,
                    movementY: event.movementY || 0,
                    buttons: event.buttons,
                    button: event.button,
                    // Detailed timing information
                    timeStamp: event.timeStamp,
                    // Event phase
                    eventPhase: event.eventPhase,
                    // Is trusted event (generated by user vs script)
                    isTrusted: event.isTrusted
                };
                
                this.mouseEvents.push(mouseEvent);
                this.lastMouseEvent = mouseEvent;
                
                // Send to backend immediately
                this.sendMouseEventToBackend(mouseEvent);
                
                // Keep events for 30 seconds max
                this.cleanupOldEvents();
            }
            
            cleanupOldEvents() {
                const now = performance.now();
                this.mouseEvents = this.mouseEvents.filter(event => 
                    now - event.timestamp < 30000 // 30 seconds
                );
            }
            
            async sendMouseEventToBackend(mouseEvent) {
                try {
                    const response = await fetch(`/api/sessions/${this.sessionId}/track-actual-mouse`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({
                            mouse_event: mouseEvent,
                            timestamp: new Date().toISOString(),
                            page_url: window.location.href,
                            user_agent: navigator.userAgent
                        })
                    });
                    
                    if (!response.ok) {
                        console.warn('Failed to send mouse event to backend');
                    }
                } catch (error) {
                    console.error('Error sending mouse event:', error);
                }
            }
            
            // Method to check if mouse is currently generating events
            isMouseActive() {
                if (!this.lastMouseEvent) return false;
                
                const timeSinceLastEvent = performance.now() - this.lastMouseEvent.timestamp;
                return timeSinceLastEvent < 5000; // Active if event in last 5 seconds
            }
            
            // Get recent mouse activity summary
            getMouseActivitySummary() {
                const now = performance.now();
                const recentEvents = this.mouseEvents.filter(event => 
                    now - event.timestamp < 10000 // Last 10 seconds
                );
                
                return {
                    total_events: recentEvents.length,
                    move_events: recentEvents.filter(e => e.type === 'mousemove').length,
                    click_events: recentEvents.filter(e => e.type.includes('click') || e.type.includes('mouseup')).length,
                    last_event_time: this.lastMouseEvent ? now - this.lastMouseEvent.timestamp : null
                };
            }
        }

        // Initialize the tracker only when sessionId is available
        let mouseTracker = null;

        function initializeMouseTracker(sessionId) {
            if (sessionId && !mouseTracker) {
                mouseTracker = new PreciseMouseTracker(sessionId);
                mouseTracker.startTracking();
            }
        }

    
        
        class ComprehensiveBandwidthManager {
            constructor(sessionId) {
                this.sessionId = sessionId;
                this.currentBandwidth = 0; // Start at 0, will be measured
                this.qualityProfile = 'unknown';
                this.monitoringInterval = null;
                this.heartbeatInterval = null;
                this.batchViolations = false;
                this.faceStream = null;
                this.audioStream = null;
            }
            

            async start() {
                try {
                    try { updateStatus('bandwidth', 'initializing', 'Measuring connection quality'); } catch(e){}
                    await this.startBandwidthManagement();
                    try { updateStatus('bandwidth', 'active', `Measured ~${Math.round(this.currentBandwidth)} kbps`); } catch(e){}
                } catch (err) {
                    console.error('Bandwidth manager start error:', err);
                    try { updateStatus('bandwidth', 'error', 'Bandwidth manager failed'); } catch(e){}
                    // do not rethrow — let the rest of the app continue
                }
            }

            
            async startBandwidthManagement() {
                // Start comprehensive bandwidth management
                // Initial bandwidth assessment
                await this.measureBandwidth();
                
                // Apply optimal settings for current conditions
                await this.applyOptimalSettings();
                
                // Continuously monitor and adjust
                this.monitoringInterval = setInterval(
                    async () => await this.monitorAndAdjust(),
                    10000  // Check every 30 seconds
                );
            }

            
            async measureBandwidth() {
                try {
                    try { updateStatus('bandwidth', 'initializing', 'Measuring connection quality'); } catch(e){}

                    const measures = [];

                    // Timeout helper
                    const timeoutMs = 6000;

                    // Technique 1 - download test with timeout
                    const controller1 = new AbortController();
                    const timeout1 = setTimeout(() => controller1.abort(), timeoutMs);
                    const startTime = Date.now();
                    try {
                        const response = await fetch(`/api/bandwidth-test?size=100000`, { signal: controller1.signal, cache: 'no-store' });
                        await response.arrayBuffer();
                        const downloadTime = Date.now() - startTime;
                        if (downloadTime > 0) {
                            const speed1 = (100000 * 8) / (downloadTime / 1000); // bits/sec
                            measures.push(speed1 / 1024); // kbps
                        }
                    } catch (e) {
                        console.warn('Download measurement failed or timed out', e);
                    } finally {
                        clearTimeout(timeout1);
                    }

                    // Technique 2 - ping latency (safe, with timeout)
                    try {
                        const pingTimes = [];
                        for (let i = 0; i < 3; i++) {
                            const pingController = new AbortController();
                            const t = setTimeout(() => pingController.abort(), 3000);
                            const pingStart = Date.now();
                            try {
                                await fetch('/api/ping', { method: 'HEAD', cache: 'no-store', signal: pingController.signal });
                                pingTimes.push(Date.now() - pingStart);
                            } catch (e) {
                                console.warn('Ping attempt failed', e);
                            } finally {
                                clearTimeout(t);
                            }
                        }
                        if (pingTimes.length) {
                            const avgPing = pingTimes.reduce((a, b) => a + b, 0) / pingTimes.length;
                            const speed2 = (1000000 / Math.max(avgPing, 1)) * 0.8; // heuristic fallback
                            measures.push(speed2 / 1024);
                        }
                    } catch (e) {
                        console.warn('Ping-based estimate failed', e);
                    }

                    if (measures.length) {
                        this.currentBandwidth = Math.max(64, Math.min(...measures)); // conservative floor
                        console.log(`Bandwidth measured: ${Math.round(this.currentBandwidth)} kbps`);
                        try { updateStatus('bandwidth', 'active', `Measured ~${Math.round(this.currentBandwidth)} kbps`); } catch(e){}
                    } else {
                        throw new Error('No bandwidth measures available');
                    }

                } catch (error) {
                    console.warn('Bandwidth measurement failed, using conservative estimate', error);
                    this.currentBandwidth = 256; // fallback
                    try { updateStatus('bandwidth', 'error', 'Bandwidth check failed — using safe settings'); } catch(e){}
                }
            }

            
            async applyOptimalSettings() {
                // Apply optimal settings for all components based on bandwidth
                const profile = this.getQualityProfile();
                
                // 1. Screen Recording Settings (Biggest Impact)
                await this.adjustRecordingQuality(profile);
                
                // 2. Screenshot Settings
                await this.adjustScreenshotSettings(profile);
                
                // 3. Face Camera Settings
                await this.adjustFaceCameraQuality(profile);
                
                // 4. Audio Settings
                await this.adjustAudioQuality(profile);
                
                // 5. Data Sync Settings
                await this.adjustDataSyncStrategy(profile);
            }
            
            getQualityProfile() {
                // Determine quality profile based on available bandwidth
                if (this.currentBandwidth < 256) {
                    return 'very_low';
                } else if (this.currentBandwidth < 512) {
                    return 'low';
                } else if (this.currentBandwidth < 1024) {
                    return 'medium';
                } else if (this.currentBandwidth < 2048) {
                    return 'high';
                } else {
                    return 'very_high';
                }
            }
            
            async adjustRecordingQuality(profile) {
                // Adjust screen recording quality - BIGGEST BANDWIDTH SAVER
                const configs = {
                    'very_low': {fps: 3, quality: 30, resolution: '640x480', bitrate: 100},
                    'low': {fps: 5, quality: 50, resolution: '800x600', bitrate: 200},
                    'medium': {fps: 8, quality: 65, resolution: '1024x768', bitrate: 350},
                    'high': {fps: 12, quality: 75, resolution: '1280x720', bitrate: 500},
                    'very_high': {fps: 15, quality: 85, resolution: '1280x720', bitrate: 800}
                };
                
                const config = configs[profile];
                
                // Send to recording endpoint
                await fetch(`/api/sessions/${this.sessionId}/recording-quality`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                
                console.log(`Recording quality set to: ${profile} (${config.bitrate}kbps)`);
            }
            
            async adjustScreenshotSettings(profile) {
                // Adjust screenshot capture settings
                const intervals = {
                    'very_low': 30000,  // 30 seconds
                    'low': 20000,       // 20 seconds  
                    'medium': 15000,    // 15 seconds
                    'high': 10000,      // 10 seconds
                    'very_high': 5000   // 5 seconds
                };
                
                const qualities = {
                    'very_low': 40,
                    'low': 50,
                    'medium': 60, 
                    'high': 70,
                    'very_high': 80
                };
                
                await fetch(`/api/sessions/${this.sessionId}/screenshot-settings`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        interval: intervals[profile],
                        quality: qualities[profile]
                    })
                });
            }
            
            async adjustFaceCameraQuality(profile) {
                // Adjust face camera quality if using one
                const constraints = {
                    'very_low': {width: 320, height: 240, frameRate: 5},
                    'low': {width: 426, height: 240, frameRate: 10},
                    'medium': {width: 640, height: 360, frameRate: 15},
                    'high': {width: 854, height: 480, frameRate: 20},
                    'very_high': {width: 1280, height: 720, frameRate: 25}
                };
                
                // This would renegotiate the media stream with new constraints
                if (this.faceStream) {
                    const track = this.faceStream.getVideoTracks()[0];
                    await track.applyConstraints(constraints[profile]);
                }
            }
            
            async adjustAudioQuality(profile) {
                // Adjust audio recording quality
                const audioConstraints = {
                    'very_low': {sampleRate: 8000, channelCount: 1, volume: 0.3},
                    'low': {sampleRate: 16000, channelCount: 1, volume: 0.5},
                    'medium': {sampleRate: 22050, channelCount: 1, volume: 0.7},
                    'high': {sampleRate: 44100, channelCount: 1, volume: 0.9},
                    'very_high': {sampleRate: 44100, channelCount: 2, volume: 1.0}
                };
                
                if (this.audioStream) {
                    const track = this.audioStream.getAudioTracks()[0];
                    await track.applyConstraints(audioConstraints[profile]);
                }
            }
            
            async adjustDataSyncStrategy(profile) {
                // Adjust how often data is synced to server
                const syncIntervals = {
                    'very_low': 60000,  // 1 minute
                    'low': 45000,       // 45 seconds
                    'medium': 30000,    // 30 seconds
                    'high': 20000,      // 20 seconds
                    'very_high': 10000  // 10 seconds
                };
                
                // Adjust heartbeat interval
                if (this.heartbeatInterval) {
                    clearInterval(this.heartbeatInterval);
                    this.heartbeatInterval = setInterval(
                        () => this.sendHeartbeat(),
                        syncIntervals[profile]
                    );
                }
                
                // Adjust violation reporting (batch them in low bandwidth)
                this.batchViolations = profile === 'very_low' || profile === 'low';
            }
            
            async monitorAndAdjust() {
                // Continuously monitor and adjust quality
                const previousBandwidth = this.currentBandwidth;
                await this.measureBandwidth();
                
                // Only adjust if bandwidth changed significantly (>20%)
                if (Math.abs(this.currentBandwidth - previousBandwidth) / previousBandwidth > 0.2) {
                    await this.applyOptimalSettings();
                    
                    // Show notification to user
                    this.showBandwidthNotification(
                        `Network quality ${this.currentBandwidth > previousBandwidth ? 'improved' : 'degraded'}. ` +
                        `Adjusting proctoring settings for ${this.getQualityProfile()} quality.`
                    );
                }
            }
            
            showBandwidthNotification(message) {
                // Show temporary notification to user
                const notification = document.createElement('div');
                notification.style.cssText = `
                    position: fixed; bottom: 20px; right: 20px; 
                    background: #3498db; color: white; padding: 12px 16px;
                    border-radius: 5px; z-index: 10000; max-width: 300px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                `;
                notification.textContent = message;
                document.body.appendChild(notification);
                
                setTimeout(() => {
                    document.body.removeChild(notification);
                }, 5000);
            }
        }

    </script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")