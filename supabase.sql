-- Motor Predictivo de Abastecimiento: tabla de pronósticos guardados por usuario
create table if not exists public.pronosticos (
    id              uuid primary key default gen_random_uuid(),
    usuario         text not null,              -- correo del usuario (login con Google)
    nombre          text not null,
    creado          timestamptz not null default now(),
    actualizado     timestamptz not null default now(),
    archivo_nombre  text,
    clave_modelo    text not null,              -- huella de los datos (modelo en Storage)
    frecuencia      text,
    n_entidades     integer,
    horizonte       integer,
    error_pct       double precision,
    config          jsonb not null default '{}'::jsonb,   -- columnas y opciones con que se prepararon los datos
    politica        jsonb,                                  -- nivel de servicio, revisión, lead time e inventario
    escenarios      jsonb not null default '[]'::jsonb      -- escenarios guardados
);
create index if not exists pronosticos_usuario_idx on public.pronosticos (usuario, creado desc);

-- Solo el servidor del sitio (service_role) accede. Sin políticas, la clave pública no puede leer nada.
alter table public.pronosticos enable row level security;

-- Permisos explícitos: solo el servidor (service_role) puede usar la tabla, aunque el proyecto
-- no exponga automáticamente las tablas nuevas a la API.
grant usage on schema public to service_role;
grant select, insert, update, delete on table public.pronosticos to service_role;
revoke all on table public.pronosticos from anon, authenticated;


-- Datos en vivo: claves de integración y filas que llegan después de entrenar
create table if not exists public.integraciones (
    registro_id  uuid primary key references public.pronosticos(id) on delete cascade,
    usuario      text not null,
    clave_hash   text not null unique,       -- sha256 de la clave; la clave no se guarda
    prefijo      text not null,              -- primeros caracteres, para reconocerla
    activa       boolean not null default true,
    creado       timestamptz not null default now(),
    ultimo_uso   timestamptz
);

create table if not exists public.datos_vivo (
    id           bigint generated always as identity primary key,
    registro_id  uuid not null references public.pronosticos(id) on delete cascade,
    usuario      text not null,
    fecha        date not null,
    entidad      text not null default '',
    objetivo     double precision,
    inventario   double precision,
    precio       double precision,
    origen       text not null default 'api',  -- api / formulario
    recibido     timestamptz not null default now(),
    unique (registro_id, fecha, entidad)
);
create index if not exists datos_vivo_registro_idx on public.datos_vivo (registro_id, fecha);

alter table public.integraciones enable row level security;
alter table public.datos_vivo enable row level security;
grant select, insert, update, delete on table public.integraciones to service_role;
grant select, insert, update, delete on table public.datos_vivo to service_role;
grant usage, select on all sequences in schema public to service_role;
revoke all on table public.integraciones from anon, authenticated;
revoke all on table public.datos_vivo from anon, authenticated;
