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
