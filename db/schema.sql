-- Esquema de AcademIA (SQLite) - normalizado hasta 3FN.
-- Alcance firmado: tareas, notas, eventos, horario, historial/malla y
-- resumen semanal. Finanzas y carne NO forman parte del alcance.
--
-- Cambios respecto a la version anterior (por que cumple 1FN/2FN/3FN):
--  * 1FN: los docentes ya no se guardan como texto separado por comas dentro
--    de materia; son filas de `docente` unidas por `materia_docente` (N:M).
--  * 2FN: `entregada` depende del par (tarea, estudiante), no de la tarea sola,
--    asi que vive en `entrega`. `nota` (peso del componente) se fusiono en
--    `componente_nota`, que ya guardaba el mismo hecho.
--  * 3FN: se quitaron los datos derivados (total_adeudado/total_pagado) y los
--    datos del docente que dependian del docente y no de la materia.
--  * Integridad: claves foraneas en todas las relaciones, UNIQUE donde
--    corresponde (evita duplicados al re-sincronizar) y ON DELETE CASCADE.

CREATE TABLE IF NOT EXISTS estudiante (
    id INTEGER PRIMARY KEY,              -- userid de Moodle (unico por persona)
    nombre TEXT NOT NULL,
    sga_alumno_id TEXT,
    moodle_user_id INTEGER,
    fecha_sincronizacion TEXT
);

CREATE TABLE IF NOT EXISTS docente (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    email TEXT,
    UNIQUE (nombre, email)
);

CREATE TABLE IF NOT EXISTS materia (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    moodle_course_id INTEGER UNIQUE,
    semestre INTEGER,
    creditos INTEGER
);

-- N:M entre materia y docente (una materia puede tener varios docentes)
CREATE TABLE IF NOT EXISTS materia_docente (
    materia_id INTEGER NOT NULL,
    docente_id INTEGER NOT NULL,
    PRIMARY KEY (materia_id, docente_id),
    FOREIGN KEY (materia_id) REFERENCES materia(id) ON DELETE CASCADE,
    FOREIGN KEY (docente_id) REFERENCES docente(id) ON DELETE CASCADE
);

-- Relacion N:M recursiva: una materia puede requerir varias, y ser
-- requisito de varias otras
CREATE TABLE IF NOT EXISTS prerrequisito (
    materia_id INTEGER NOT NULL,
    materia_requisito_id INTEGER NOT NULL,
    PRIMARY KEY (materia_id, materia_requisito_id),
    FOREIGN KEY (materia_id) REFERENCES materia(id) ON DELETE CASCADE,
    FOREIGN KEY (materia_requisito_id) REFERENCES materia(id) ON DELETE CASCADE
);

-- Una matricula = un estudiante cursando una materia en un periodo.
-- nota_final y asistencia vienen del ERP (SGA).
CREATE TABLE IF NOT EXISTS matricula (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estudiante_id INTEGER NOT NULL,
    materia_id INTEGER NOT NULL,
    periodo TEXT,
    estado TEXT CHECK(estado IN ('aprobada', 'reprobada', 'en_curso', 'pendiente')),
    nota_final REAL,
    asistencia REAL,
    UNIQUE (estudiante_id, materia_id, periodo),
    FOREIGN KEY (estudiante_id) REFERENCES estudiante(id) ON DELETE CASCADE,
    FOREIGN KEY (materia_id) REFERENCES materia(id) ON DELETE CASCADE
);

-- Calificaciones detalladas de Moodle por componente (tareas, N1, Total N2...)
-- Se guardan como TEXT a proposito: Moodle entrega valores como "10,00" o "-"
-- (aun sin calificar) y se muestran tal cual.
CREATE TABLE IF NOT EXISTS componente_nota (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    matricula_id INTEGER NOT NULL,
    nombre_item TEXT NOT NULL,
    calificacion TEXT,
    porcentaje TEXT,
    FOREIGN KEY (matricula_id) REFERENCES matricula(id) ON DELETE CASCADE
);

-- La tarea es de la materia (compartida); el estado de entrega es de cada
-- estudiante.
CREATE TABLE IF NOT EXISTS tarea (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    materia_id INTEGER NOT NULL,
    moodle_assign_id INTEGER,
    nombre TEXT NOT NULL,
    fecha_entrega TEXT,
    UNIQUE (materia_id, moodle_assign_id),
    FOREIGN KEY (materia_id) REFERENCES materia(id) ON DELETE CASCADE
);

-- Estado de entrega de una tarea para un estudiante, tal como lo informa Moodle.
-- LA AUSENCIA DE FILA SIGNIFICA "SIN VERIFICAR": no se asume ni pendiente ni entregada.
-- Solo se guarda una fila cuando Moodle dio un estado reconocible; si una consulta
-- posterior falla, la fila anterior se conserva (verificada_en indica cuando se confirmo).
CREATE TABLE IF NOT EXISTS entrega (
    tarea_id INTEGER NOT NULL,
    estudiante_id INTEGER NOT NULL,
    estado TEXT NOT NULL CHECK(estado IN ('entregada', 'borrador', 'sin_entregar')),
    verificada_en TEXT,
    PRIMARY KEY (tarea_id, estudiante_id),
    FOREIGN KEY (tarea_id) REFERENCES tarea(id) ON DELETE CASCADE,
    FOREIGN KEY (estudiante_id) REFERENCES estudiante(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estudiante_id INTEGER,
    materia_id INTEGER,
    nombre TEXT NOT NULL,
    tipo TEXT,              -- ej: "examen", "entrega", "otro"
    fecha TEXT NOT NULL,
    FOREIGN KEY (estudiante_id) REFERENCES estudiante(id) ON DELETE CASCADE,
    FOREIGN KEY (materia_id) REFERENCES materia(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS horario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    materia_id INTEGER NOT NULL,
    dia TEXT,               -- lunes, martes, ...
    hora_inicio TEXT,
    hora_fin TEXT,
    aula TEXT,
    FOREIGN KEY (materia_id) REFERENCES materia(id) ON DELETE CASCADE
);

-- Sesiones web (login por token). Los datos van CIFRADOS y el id de la
-- cookie se guarda solo como hash; ver web/sessions_store.py.
CREATE TABLE IF NOT EXISTS sesion_web (
    sid_hash TEXT PRIMARY KEY,
    estudiante_id INTEGER,
    datos_cifrados BLOB NOT NULL,
    creada REAL NOT NULL,
    expira REAL NOT NULL,
    FOREIGN KEY (estudiante_id) REFERENCES estudiante(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sesion_extraccion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estudiante_id INTEGER NOT NULL,
    plataforma TEXT CHECK(plataforma IN ('sga', 'moodle')),
    fecha TEXT NOT NULL,
    estado TEXT CHECK(estado IN ('exitosa', 'fallida')),
    detalle TEXT,
    FOREIGN KEY (estudiante_id) REFERENCES estudiante(id) ON DELETE CASCADE
);

-- Espacio de estudio: una fila por practica terminada. Solo guarda el resultado
-- (cuantas acertó), nunca el texto de las preguntas ni las respuestas.
CREATE TABLE IF NOT EXISTS practica (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estudiante_id INTEGER NOT NULL,
    materia_id INTEGER NOT NULL,
    tema TEXT,
    fecha TEXT NOT NULL,
    total INTEGER NOT NULL CHECK(total > 0),
    aciertos INTEGER NOT NULL CHECK(aciertos >= 0 AND aciertos <= total),
    FOREIGN KEY (estudiante_id) REFERENCES estudiante(id) ON DELETE CASCADE,
    FOREIGN KEY (materia_id) REFERENCES materia(id) ON DELETE CASCADE
);

-- Detector de cambios (Fase 1).
-- foto_estado guarda UNA foto por estudiante y plataforma: el estado tal como quedo tras la
-- ultima sincronizacion EXITOSA. Una sincronizacion fallida nunca la toca, asi que un fallo
-- no se confunde con un cambio en los datos. 'datos' es JSON.
CREATE TABLE IF NOT EXISTS foto_estado (
    estudiante_id INTEGER NOT NULL,
    plataforma TEXT NOT NULL CHECK(plataforma IN ('sga', 'moodle')),
    creada_en TEXT NOT NULL,
    datos TEXT NOT NULL,
    PRIMARY KEY (estudiante_id, plataforma),
    FOREIGN KEY (estudiante_id) REFERENCES estudiante(id) ON DELETE CASCADE
);

-- cambio: lo que difiere entre dos fotos consecutivas. visto_en NULL = aun es una novedad.
-- La primera sincronizacion solo guarda la foto de partida (no hay con que comparar).
CREATE TABLE IF NOT EXISTS cambio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estudiante_id INTEGER NOT NULL,
    plataforma TEXT NOT NULL CHECK(plataforma IN ('sga', 'moodle')),
    tipo TEXT NOT NULL CHECK(tipo IN (
        'tarea_nueva', 'tarea_fecha', 'tarea_estado',
        'nota_nueva', 'nota_cambio',
        'evento_nuevo', 'evento_fecha',
        'sga_nota_final', 'sga_asistencia')),
    materia_id INTEGER,
    item TEXT,
    antes TEXT,
    despues TEXT,
    detectado_en TEXT NOT NULL,
    visto_en TEXT,
    FOREIGN KEY (estudiante_id) REFERENCES estudiante(id) ON DELETE CASCADE,
    FOREIGN KEY (materia_id) REFERENCES materia(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cambio_estudiante ON cambio(estudiante_id, visto_en, detectado_en);

CREATE INDEX IF NOT EXISTS idx_practica_estudiante_materia ON practica(estudiante_id, materia_id);
CREATE INDEX IF NOT EXISTS idx_matricula_estudiante ON matricula(estudiante_id);
CREATE INDEX IF NOT EXISTS idx_matricula_materia ON matricula(materia_id);
CREATE INDEX IF NOT EXISTS idx_componente_matricula ON componente_nota(matricula_id);
CREATE INDEX IF NOT EXISTS idx_tarea_materia ON tarea(materia_id);
CREATE INDEX IF NOT EXISTS idx_evento_estudiante ON evento(estudiante_id);
CREATE INDEX IF NOT EXISTS idx_entrega_estudiante ON entrega(estudiante_id);
CREATE INDEX IF NOT EXISTS idx_horario_materia ON horario(materia_id);
CREATE INDEX IF NOT EXISTS idx_extraccion_estudiante ON sesion_extraccion(estudiante_id);
