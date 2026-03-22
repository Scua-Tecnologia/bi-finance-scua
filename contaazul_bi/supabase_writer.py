"""
supabase_writer.py
──────────────────
Escreve as tabelas de analytics geradas pelo ETL no Supabase (PostgreSQL)
usando SQLAlchemy + pandas.to_sql().

Cada execução do pipeline substitui as tabelas (if_exists='replace'), garantindo
que os dados no banco sempre reflitam a última extração completa.

Uso:
    from contaazul_bi.supabase_writer import write_analytics_to_supabase
    write_analytics_to_supabase(analytics.tables, database_url)
"""

from __future__ import annotations

import json
import logging

import pandas as pd
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)

# Tabelas do star schema que devem ser escritas no Supabase.
# Tabelas não listadas aqui são ignoradas.
ANALYTICS_TABLES: frozenset[str] = frozenset(
    {
        "dim_categoria",
        "dim_categoria_dre",
        "dim_centro_custo",
        "dim_conta_financeira",
        "dim_pessoa",
        "dim_calendario",
        "fato_saldos_contas",
        "fato_contas_a_receber",
        "fato_contas_a_pagar",
        "fato_transferencias",
        "fato_vendas",
        "fato_contratos",
        "fato_baixas",
        "fato_fluxo_caixa_realizado",
        "fato_fluxo_caixa_compromissado",
        "fato_financeiro_consolidado",
    }
)


def _serialize_complex_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Converte colunas do tipo object que contêm dicts ou lists para strings JSON,
    pois o PostgreSQL não aceita objetos Python nativos como TEXT.
    """
    out = df.copy()
    for col in out.select_dtypes(include="object").columns:
        sample = out[col].dropna().head(10)
        if sample.apply(lambda v: isinstance(v, (dict, list))).any():
            out[col] = out[col].apply(
                lambda v: json.dumps(v, ensure_ascii=False, default=str)
                if isinstance(v, (dict, list))
                else v
            )
    return out


def _normalize_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza tipos pandas que o SQLAlchemy não mapeia diretamente para PostgreSQL:
    - Nullable integers (Int64, etc.) → float64 (PostgreSQL aceita NULL em FLOAT)
    - Datetime com timezone → sem timezone (evita conflitos com colunas TIMESTAMP)
    """
    out = df.copy()

    nullable_int_types = {"Int8", "Int16", "Int32", "Int64", "UInt8", "UInt16", "UInt32", "UInt64"}
    for col in out.columns:
        if str(out[col].dtype) in nullable_int_types:
            out[col] = out[col].astype("float64")

    for col in out.select_dtypes(include=["datetimetz"]).columns:
        out[col] = out[col].dt.tz_localize(None)

    return out


def write_analytics_to_supabase(tables: dict[str, pd.DataFrame], database_url: str) -> None:
    """
    Escreve as tabelas de analytics no Supabase.

    Args:
        tables: Dicionário {nome_tabela: DataFrame} retornado por build_analytics().
        database_url: Connection string PostgreSQL do Supabase
                      (ex: postgresql://postgres:[senha]@db.[ref].supabase.co:5432/postgres)
    """
    engine = create_engine(database_url, pool_pre_ping=True)
    written = 0
    skipped = 0

    try:
        for table_name, df in tables.items():
            if table_name not in ANALYTICS_TABLES:
                continue

            if df.empty:
                logger.warning("Supabase: tabela '%s' vazia — ignorada.", table_name)
                skipped += 1
                continue

            df_out = _normalize_dtypes(_serialize_complex_columns(df))

            df_out.to_sql(
                table_name,
                engine,
                schema="public",
                if_exists="replace",
                index=False,
                chunksize=500,
            )

            logger.info("Supabase: '%s' → %s linhas escritas.", table_name, len(df_out))
            written += 1

    finally:
        engine.dispose()

    logger.info(
        "Supabase: escrita concluída. %s tabelas escritas, %s ignoradas.",
        written,
        skipped,
    )
