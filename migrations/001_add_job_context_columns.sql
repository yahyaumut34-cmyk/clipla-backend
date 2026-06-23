-- Migration 001: Job context columns for persistent storage
-- Supabase SQL Editor'de çalıştır

ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS video_path text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS context jsonb DEFAULT '{}';
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS last_edited_path text;

-- Edit versions tablosu
CREATE TABLE IF NOT EXISTS public.edit_versions (
  id            bigserial primary key,
  job_id        text references public.jobs(id),
  version       int not null,
  command_text  text,
  output_file   text,
  duration      float,
  created_at    timestamptz default now()
);

ALTER TABLE public.edit_versions ENABLE ROW LEVEL SECURITY;
