-- ─── Migration 001 — Estrutura inicial do Supabase ────────────────────────────
-- Execute este script uma única vez no SQL Editor do Supabase Dashboard.
-- As tabelas de analytics (fato_*, dim_*) são criadas automaticamente pelo
-- ETL pipeline via pandas.to_sql() a cada execução — não precisam de migration.

-- ─── Cenários de caixa ────────────────────────────────────────────────────────
-- Substitui: output/cenarios.json
-- Estrutura: tabela de linha única (id=1 sempre) com colunas JSONB.

CREATE TABLE IF NOT EXISTS bi_cenarios (
    id               INTEGER PRIMARY KEY DEFAULT 1,
    projecoes        JSONB    NOT NULL DEFAULT '[]',
    renovacao_ativa  BOOLEAN  NOT NULL DEFAULT true,
    contratos_excluidos JSONB NOT NULL DEFAULT '[]',
    atualizado_em    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT bi_cenarios_single_row CHECK (id = 1)
);

-- Linha padrão (inserida apenas se a tabela estiver vazia)
INSERT INTO bi_cenarios (id, projecoes, renovacao_ativa, contratos_excluidos)
VALUES (1, '[]', true, '[]')
ON CONFLICT (id) DO NOTHING;

-- ─── Tokens OAuth do Conta Azul ───────────────────────────────────────────────
-- Substitui: .secrets/conta_azul_tokens.json
-- Usado na Fase 3 para eliminar dependência de arquivo local no servidor.

-- Campos espelham exatamente o dataclass OAuthTokenBundle para serialização direta.
CREATE TABLE IF NOT EXISTS bi_oauth_tokens (
    id            INTEGER          PRIMARY KEY DEFAULT 1,
    access_token  TEXT             NOT NULL DEFAULT '',
    refresh_token TEXT,
    token_type    TEXT             NOT NULL DEFAULT 'Bearer',
    expires_in    INTEGER          NOT NULL DEFAULT 3600,
    obtained_at   DOUBLE PRECISION NOT NULL DEFAULT 0,
    atualizado_em TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CONSTRAINT bi_oauth_tokens_single_row CHECK (id = 1)
);

INSERT INTO bi_oauth_tokens (id, access_token, token_type, expires_in, obtained_at)
VALUES (1, '', 'Bearer', 3600, 0)
ON CONFLICT (id) DO NOTHING;

-- ─── Row Level Security ───────────────────────────────────────────────────────
-- As tabelas de analytics (fato_*, dim_*) são substituídas a cada ETL, então
-- RLS não é aplicado a elas (seriam dropadas e recriadas com permissões default).
-- Para bi_cenarios e bi_oauth_tokens, habilite RLS e restrinja ao service_role:

ALTER TABLE bi_cenarios      ENABLE ROW LEVEL SECURITY;
ALTER TABLE bi_oauth_tokens  ENABLE ROW LEVEL SECURITY;

-- Permite leitura e escrita apenas via service_role (backend/ETL)
-- O dashboard usa a DATABASE_URL com service_role key, então tem acesso total.
CREATE POLICY "service_role full access" ON bi_cenarios
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "service_role full access" ON bi_oauth_tokens
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
