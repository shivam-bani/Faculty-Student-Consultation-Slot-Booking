-- CampusSync database schema (MySQL 8.4)
--
-- Run as root from the project folder:
--     mysql -u root -p
--     mysql> SOURCE schema.sql;
--
-- Running it a second time stops with "Table ... already exists" (nothing is overwritten).
-- To rebuild from scratch during development (DELETES ALL DATA):
--     mysql> DROP DATABASE campussync;
--
-- Rules that involve more than one row or table (seats left in a session, "this user is a
-- faculty member", base priority from the student's semester) cannot be CHECK constraints,
-- because a CHECK only sees the row being written. The Flask backend enforces those inside
-- transactions.

CREATE DATABASE IF NOT EXISTS campussync
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE campussync;

-- ---------------------------------------------------------------------------------------
-- Accounts
-- ---------------------------------------------------------------------------------------

CREATE TABLE users (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name          VARCHAR(100) NOT NULL,
  email         VARCHAR(254) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,                  -- never the password itself
  role          ENUM('student', 'faculty', 'admin') NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,          -- removing faculty = deactivate
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT pk_users PRIMARY KEY (id),
  CONSTRAINT uq_users_email UNIQUE (email),
  CONSTRAINT chk_users_name CHECK (CHAR_LENGTH(TRIM(name)) > 0),
  CONSTRAINT chk_users_student_email CHECK (role <> 'student' OR email LIKE '%@geu.ac.in')
);

-- One saved profile per student (filled in from the booking form).
CREATE TABLE student_profiles (
  user_id    INT UNSIGNED NOT NULL,
  student_id VARCHAR(20) NOT NULL,
  roll_no    VARCHAR(20) NOT NULL,
  course     VARCHAR(30) NOT NULL,
  semester   ENUM('I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII') NOT NULL,
  section    VARCHAR(10) NOT NULL,
  group_id   VARCHAR(30) NULL,                          -- PBL group; NULL = not in a group
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT pk_student_profiles PRIMARY KEY (user_id),
  CONSTRAINT fk_profiles_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT uq_profiles_student_id UNIQUE (student_id),
  CONSTRAINT uq_profiles_roll_no UNIQUE (roll_no),
  CONSTRAINT chk_profiles_ids CHECK (CHAR_LENGTH(student_id) >= 3 AND CHAR_LENGTH(roll_no) >= 3)
);

-- ---------------------------------------------------------------------------------------
-- Timetable (imported once from timetable-data.js, then edited by the admin)
-- ---------------------------------------------------------------------------------------

CREATE TABLE timetable_profiles (
  id     VARCHAR(80)  NOT NULL,                         -- e.g. 'as-shukla'
  name   VARCHAR(120) NOT NULL,
  source ENUM('imported', 'admin') NOT NULL,
  edited BOOLEAN NOT NULL DEFAULT FALSE,                -- TRUE = keep admin edits on re-import
  CONSTRAINT pk_timetable_profiles PRIMARY KEY (id)
);

CREATE TABLE timetable_classes (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  profile_id  VARCHAR(80)  NOT NULL,
  day_of_week TINYINT UNSIGNED NOT NULL,                -- 1 = Monday ... 6 = Saturday
  start_time  TIME NOT NULL,
  end_time    TIME NOT NULL,
  code        VARCHAR(20)  NOT NULL,                    -- subject code, or 'Busy'
  subject     VARCHAR(150) NOT NULL DEFAULT '',
  section     VARCHAR(255) NOT NULL DEFAULT '',
  room        VARCHAR(100) NOT NULL DEFAULT '',
  CONSTRAINT pk_timetable_classes PRIMARY KEY (id),
  CONSTRAINT fk_classes_profile FOREIGN KEY (profile_id) REFERENCES timetable_profiles (id) ON DELETE CASCADE,
  CONSTRAINT chk_classes_day CHECK (day_of_week BETWEEN 1 AND 6),
  CONSTRAINT chk_classes_time CHECK (end_time > start_time),
  INDEX idx_classes_profile_day (profile_id, day_of_week)
);

-- Per-faculty consultation settings (was campussync_availability in localStorage).
CREATE TABLE faculty_settings (
  faculty_id INT UNSIGNED NOT NULL,
  profile_id VARCHAR(80) NULL,                          -- linked timetable; UNIQUE = one account per timetable
  slot_len   TINYINT UNSIGNED NOT NULL DEFAULT 30,      -- minutes
  capacity   TINYINT UNSIGNED NOT NULL DEFAULT 3,       -- students per slot
  CONSTRAINT pk_faculty_settings PRIMARY KEY (faculty_id),
  CONSTRAINT fk_settings_faculty FOREIGN KEY (faculty_id) REFERENCES users (id),
  CONSTRAINT fk_settings_profile FOREIGN KEY (profile_id) REFERENCES timetable_profiles (id) ON DELETE SET NULL,
  CONSTRAINT uq_settings_profile UNIQUE (profile_id),
  CONSTRAINT chk_settings_slot_len CHECK (slot_len IN (15, 20, 30, 45, 55)),
  CONSTRAINT chk_settings_capacity CHECK (capacity BETWEEN 1 AND 50)
);

-- Days on which a faculty member offers consultations (one row per day).
CREATE TABLE consult_days (
  faculty_id  INT UNSIGNED NOT NULL,
  day_of_week TINYINT UNSIGNED NOT NULL,
  CONSTRAINT pk_consult_days PRIMARY KEY (faculty_id, day_of_week),
  CONSTRAINT fk_consult_days_faculty FOREIGN KEY (faculty_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT chk_consult_days_day CHECK (day_of_week BETWEEN 1 AND 6)
);

-- Weekly free slots the admin switched off ("Not offered").
CREATE TABLE slots_off (
  faculty_id  INT UNSIGNED NOT NULL,
  day_of_week TINYINT UNSIGNED NOT NULL,
  start_time  TIME NOT NULL,
  CONSTRAINT pk_slots_off PRIMARY KEY (faculty_id, day_of_week, start_time),
  CONSTRAINT fk_slots_off_faculty FOREIGN KEY (faculty_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT chk_slots_off_day CHECK (day_of_week BETWEEN 1 AND 6)
);

-- Extra consultation hours added by the admin.
CREATE TABLE custom_slots (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  faculty_id  INT UNSIGNED NOT NULL,
  day_of_week TINYINT UNSIGNED NOT NULL,
  start_time  TIME NOT NULL,
  end_time    TIME NOT NULL,
  capacity    TINYINT UNSIGNED NOT NULL,
  CONSTRAINT pk_custom_slots PRIMARY KEY (id),
  CONSTRAINT fk_custom_slots_faculty FOREIGN KEY (faculty_id) REFERENCES users (id) ON DELETE CASCADE,
  CONSTRAINT chk_custom_slots_day CHECK (day_of_week BETWEEN 1 AND 6),
  CONSTRAINT chk_custom_slots_time CHECK (end_time > start_time),
  CONSTRAINT chk_custom_slots_capacity CHECK (capacity BETWEEN 1 AND 50)
);

-- ---------------------------------------------------------------------------------------
-- Sessions, bookings and the queue
-- ---------------------------------------------------------------------------------------

-- A dated meeting slot. Timetable slots become sessions on their first booking.
CREATE TABLE sessions (
  id           INT UNSIGNED NOT NULL AUTO_INCREMENT,
  faculty_id   INT UNSIGNED NOT NULL,
  topic        VARCHAR(150) NOT NULL DEFAULT 'Consultation hours',
  description  VARCHAR(255) NOT NULL DEFAULT '',
  session_date DATE NOT NULL,
  start_time   TIME NOT NULL,
  end_time     TIME NOT NULL,
  capacity     TINYINT UNSIGNED NOT NULL,               -- students in this slot's queue
  source       ENUM('timetable', 'created') NOT NULL,
  cancelled    BOOLEAN NOT NULL DEFAULT FALSE,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT pk_sessions PRIMARY KEY (id),
  CONSTRAINT fk_sessions_faculty FOREIGN KEY (faculty_id) REFERENCES users (id),
  -- Two students booking the same free slot at the same moment cannot create two sessions.
  CONSTRAINT uq_sessions_slot UNIQUE (faculty_id, session_date, start_time),
  CONSTRAINT chk_sessions_time CHECK (end_time > start_time),
  CONSTRAINT chk_sessions_capacity CHECK (capacity BETWEEN 1 AND 50)
);

CREATE TABLE bookings (
  id                INT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_id        INT UNSIGNED NOT NULL,
  student_id        INT UNSIGNED NOT NULL,
  token             SMALLINT UNSIGNED NOT NULL,         -- per session: 1, 2, 3 ... never reused
  topic             VARCHAR(150) NOT NULL,
  description       VARCHAR(1000) NOT NULL DEFAULT '',
  consultation_type ENUM('General', 'Major project') NOT NULL DEFAULT 'General',
  base_priority     TINYINT UNSIGNED NOT NULL DEFAULT 1, -- computed by the server at booking time
  status            ENUM('Pending', 'Waiting', 'In Session', 'Completed', 'Rejected', 'Cancelled')
                    NOT NULL DEFAULT 'Pending',         -- 'Waiting' is shown as "Accepted"
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  checked_in_at     DATETIME NULL,                      -- "I'm here": waiting (and aging) starts
  called_at         DATETIME NULL,                      -- faculty pressed "Call next"
  completed_at      DATETIME NULL,
  -- 1 while the booking holds a seat, NULL otherwise. UNIQUE ignores NULLs, so a student can
  -- book the same session again after cancelling, but cannot hold two active bookings in it.
  active_flag       TINYINT AS (IF(status IN ('Pending', 'Waiting', 'In Session'), 1, NULL)) STORED,
  -- 1 only for the student currently being seen: at most one per session (one-by-one queue).
  in_session_flag   TINYINT AS (IF(status = 'In Session', 1, NULL)) STORED,
  CONSTRAINT pk_bookings PRIMARY KEY (id),
  CONSTRAINT fk_bookings_session FOREIGN KEY (session_id) REFERENCES sessions (id),
  CONSTRAINT fk_bookings_student FOREIGN KEY (student_id) REFERENCES users (id),
  CONSTRAINT uq_bookings_token UNIQUE (session_id, token),
  CONSTRAINT uq_bookings_active_student UNIQUE (session_id, student_id, active_flag),
  CONSTRAINT uq_bookings_one_in_session UNIQUE (session_id, in_session_flag),
  CONSTRAINT chk_bookings_token CHECK (token > 0),
  CONSTRAINT chk_bookings_topic CHECK (CHAR_LENGTH(TRIM(topic)) > 0),
  CONSTRAINT chk_bookings_priority CHECK (base_priority IN (1, 2)),
  -- Priority 2 is only possible for a Major project consultation.
  CONSTRAINT chk_bookings_priority_type CHECK (base_priority = 1 OR consultation_type = 'Major project'),
  -- Nobody can be "In Session" without having checked in and been called.
  CONSTRAINT chk_bookings_in_session CHECK (status <> 'In Session' OR (checked_in_at IS NOT NULL AND called_at IS NOT NULL)),
  CONSTRAINT chk_bookings_call_order CHECK (called_at IS NULL OR checked_in_at IS NULL OR called_at >= checked_in_at),
  INDEX idx_bookings_student_status (student_id, status),
  INDEX idx_bookings_session_status (session_id, status)
);

-- A student's request to move a booking (at most one open request per booking).
CREATE TABLE reschedule_requests (
  booking_id   INT UNSIGNED NOT NULL,
  req_date     DATE NOT NULL,
  req_start    TIME NOT NULL,
  req_end      TIME NOT NULL,
  reason       VARCHAR(255) NOT NULL DEFAULT '',
  requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT pk_reschedule_requests PRIMARY KEY (booking_id),
  CONSTRAINT fk_reschedule_booking FOREIGN KEY (booking_id) REFERENCES bookings (id) ON DELETE CASCADE,
  CONSTRAINT chk_reschedule_time CHECK (req_end > req_start)
);

-- What happened to each booking, shown under "Details" on the faculty page.
CREATE TABLE booking_history (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
  booking_id INT UNSIGNED NOT NULL,
  at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  text       VARCHAR(255) NOT NULL,
  CONSTRAINT pk_booking_history PRIMARY KEY (id),
  CONSTRAINT fk_history_booking FOREIGN KEY (booking_id) REFERENCES bookings (id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------------------
-- Notifications
-- ---------------------------------------------------------------------------------------

CREATE TABLE notifications (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id    INT UNSIGNED NOT NULL,
  title      VARCHAR(100)  NOT NULL,
  message    VARCHAR(1000) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  is_read    BOOLEAN NOT NULL DEFAULT FALSE,
  CONSTRAINT pk_notifications PRIMARY KEY (id),
  CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
  INDEX idx_notifications_unread (user_id, is_read)
);

SHOW TABLES;
