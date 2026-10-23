CREATE TABLE estudiante (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, sga_alumno_id TEXT, moodle_user_id INTEGER, fecha_sincronizacion TEXT, total_adeudado REAL, total_pagado REAL);
CREATE TABLE materia (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, moodle_course_id INTEGER UNIQUE, semestre INTEGER, creditos INTEGER, docente_nombre TEXT, docente_email TEXT);
CREATE TABLE tarea (id INTEGER PRIMARY KEY AUTOINCREMENT, materia_id INTEGER, moodle_assign_id INTEGER, nombre TEXT, fecha_entrega TEXT, entregada INTEGER DEFAULT 0);
CREATE TABLE nota (id INTEGER PRIMARY KEY, matricula_id INTEGER);
CREATE TABLE rubro_financiero (id INTEGER PRIMARY KEY, estudiante_id INTEGER);
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, descripcion TEXT, aplicada TEXT NOT NULL);
INSERT INTO schema_version VALUES (1,'viejo','x');
