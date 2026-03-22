from __future__ import annotations

import ast
import logging
from dataclasses import dataclass

import pandas as pd
from pandas.api.types import is_list_like

from contaazul_bi.utils import coalesce_columns, safe_to_datetime


logger = logging.getLogger(__name__)

# Status de parcelas considerados quitados/pagos (deve ser mantido em sincronia com main.py)
PAID_STATUSES: frozenset[str] = frozenset({"PAGO", "RECEBIDO", "QUITADO"})


@dataclass(slots=True)
class AnalyticsTables:
    tables: dict[str, pd.DataFrame]


def _safe_numeric_series(df: pd.DataFrame, column_name: str) -> pd.Series:
    if column_name in df.columns:
        return pd.to_numeric(df[column_name], errors="coerce").fillna(0)
    return pd.Series(0, index=df.index, dtype="float64")


def _first_nested_value(value: object, key: str) -> object:
    if is_list_like(value) and not isinstance(value, (str, bytes, dict)):
        for item in value:
            if isinstance(item, dict) and item.get(key) is not None:
                return item.get(key)
    return None


def _nested_count(value: object) -> int:
    if is_list_like(value) and not isinstance(value, (str, bytes, dict)):
        return sum(1 for item in value if isinstance(item, dict))
    return 0


def _coerce_nested_items(value: object) -> list[dict]:
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except Exception:
            logger.warning("Falha ao interpretar valor aninhado como literal Python: %.80r", value)
            return []

    if is_list_like(value) and not isinstance(value, (str, bytes, dict)):
        return [item for item in value if isinstance(item, dict)]

    if isinstance(value, dict):
        return [value]

    return []


def _flatten_dre_categories(dre: pd.DataFrame) -> pd.DataFrame:
    if dre.empty or "subitens" not in dre.columns:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []

    for _, dre_row in dre.iterrows():
        dre_grupo = dre_row.get("descricao")
        for subitem in _coerce_nested_items(dre_row.get("subitens")):
            dre_subgrupo = subitem.get("descricao")
            for categoria in _coerce_nested_items(subitem.get("categorias_financeiras")):
                rows.append(
                    {
                        "categoria_id": categoria.get("id"),
                        "dre_grupo": dre_grupo,
                        "dre_subgrupo": dre_subgrupo,
                    }
                )

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).drop_duplicates(subset=["categoria_id"])


def _enrich_categories_with_dre(categories: pd.DataFrame, dre: pd.DataFrame) -> pd.DataFrame:
    if categories.empty:
        return categories.copy()

    out = categories.copy()
    dre_flat = _flatten_dre_categories(dre)
    if dre_flat.empty:
        return out

    out = out.merge(dre_flat, left_on="id", right_on="categoria_id", how="left")
    out = out.drop(columns=["categoria_id"], errors="ignore")

    existing_mapping = (
        out.loc[out["entrada_dre"].notna() & out["dre_subgrupo"].notna(), ["dre_subgrupo", "entrada_dre"]]
        .drop_duplicates()
        .dropna()
    )
    subgroup_to_entry = (
        existing_mapping.groupby("dre_subgrupo")["entrada_dre"].agg(lambda values: values.mode().iloc[0]).to_dict()
        if not existing_mapping.empty
        else {}
    )

    manual_entry_map = {
        "Impostos Sobre Vendas": "DEDUCOES_RECEITA_BRUTA",
        "Descontos Incondicionais": "DEDUCOES_RECEITA_BRUTA",
        "Receitas e Rendimentos Financeiros": "RECEITAS_RENDIMENTOS_FINANCEIROS",
        "Despesas Financeiras": "DESPESAS_FINANCEIRAS",
        "Outras Despesas Não Operacionais": "OUTRAS_DESPESAS_NAO_OPERACIONAIS",
        "Impostos sobre Resultado": "IMPOSTOS_SOBRE_RESULTADO",
        "Atividades de Investimento (Saídas) – Imobilizado": "ATIVIDADES_DE_INVESTIMENTO",
        "Atividades de Financiamento (Entradas)": "ATIVIDADES_DE_FINANCIAMENTO",
        "Atividades de Financiamento (Saídas) - Empréstimos": "ATIVIDADES_DE_FINANCIAMENTO",
        "Atividades de Financiamento (Saídas) - Parcelamentos": "ATIVIDADES_DE_FINANCIAMENTO",
    }

    inferred_entry = out["dre_subgrupo"].map(subgroup_to_entry).fillna(out["dre_subgrupo"].map(manual_entry_map))
    out["entrada_dre"] = out["entrada_dre"].fillna(inferred_entry)

    return out


def _enrich_acquittances_with_origin(acquittances: pd.DataFrame, financial_events: pd.DataFrame) -> pd.DataFrame:
    if acquittances.empty or financial_events.empty:
        return acquittances.copy()

    if "id_parcela" not in acquittances.columns or "id" not in financial_events.columns:
        return acquittances.copy()

    origin_columns = [
        "id",
        "descricao",
        "tipo_evento",
        "status",
        "data_vencimento",
        "data_competencia",
        "categorias",
        "centros_de_custo",
        "cliente.id",
        "cliente.nome",
        "fornecedor.id",
        "fornecedor.nome",
    ]
    available_columns = [column for column in origin_columns if column in financial_events.columns]
    if not available_columns:
        return acquittances.copy()

    origin = financial_events[available_columns].copy()
    origin["id"] = origin["id"].astype(str)

    if "categorias" in origin.columns:
        origin["categoria_id"] = origin["categorias"].apply(lambda value: _first_nested_value(value, "id"))
        origin["categoria_nome"] = origin["categorias"].apply(lambda value: _first_nested_value(value, "nome"))
        origin["quantidade_categorias"] = origin["categorias"].apply(_nested_count)

    if "centros_de_custo" in origin.columns:
        origin["centro_custo_id"] = origin["centros_de_custo"].apply(lambda value: _first_nested_value(value, "id"))
        origin["centro_custo_nome"] = origin["centros_de_custo"].apply(lambda value: _first_nested_value(value, "nome"))
        origin["quantidade_centros_custo"] = origin["centros_de_custo"].apply(_nested_count)

    origin = origin.rename(
        columns={
            "id": "id_parcela",
            "descricao": "descricao_origem",
            "tipo_evento": "tipo_evento_origem",
            "status": "status_origem",
            "data_vencimento": "data_vencimento_origem",
            "data_competencia": "data_competencia_origem",
            "cliente.id": "cliente_id_origem",
            "cliente.nome": "cliente_nome_origem",
            "fornecedor.id": "fornecedor_id_origem",
            "fornecedor.nome": "fornecedor_nome_origem",
        }
    )

    out = acquittances.copy()
    out["id_parcela"] = out["id_parcela"].astype(str)
    return out.merge(origin, on="id_parcela", how="left")


def _prepare_financial_events(df: pd.DataFrame, event_kind: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    out = df.copy()
    out["tipo_evento"] = event_kind

    for col in ["data_vencimento", "data_competencia", "data_pagamento", "atualizado_em", "criado_em"]:
        if col in out.columns:
            out[col] = safe_to_datetime(out[col])

    out = coalesce_columns(
        out,
        ["valor_composicao.valor_bruto", "valor_bruto", "valor", "valor_original", "valor_previsto"],
        "valor_documento",
    )
    out = coalesce_columns(out, ["valor_composicao.desconto", "desconto"], "valor_desconto")
    out = coalesce_columns(out, ["valor_composicao.juros", "juros"], "valor_juros")
    out = coalesce_columns(out, ["valor_composicao.multa", "multa"], "valor_multa")
    out = coalesce_columns(out, ["valor_pago", "baixas.valor_composicao.valor_bruto"], "valor_pago_total")

    sign = -1 if event_kind == "DESPESA" else 1

    out["valor_documento_sinal"] = _safe_numeric_series(out, "valor_documento") * sign
    out["valor_pago_sinal"] = _safe_numeric_series(out, "valor_pago_total") * sign

    if "status" in out.columns:
        out["status_normalizado"] = out["status"].astype(str).str.upper()
    else:
        out["status_normalizado"] = None

    if "data_vencimento" in out.columns:
        out["esta_vencido"] = (
            out["data_vencimento"].notna()
            & (out["data_vencimento"].dt.date < pd.Timestamp.today().date())
            & (~out["status_normalizado"].isin(PAID_STATUSES))
        )
    else:
        out["esta_vencido"] = False

    return out


def _prepare_acquittances(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    out = df.copy()

    out = coalesce_columns(
        out,
        [
            "data_pagamento",
            "data_baixa",
            "data_recebimento",
            "data_liquidacao",
            "baixa.data_pagamento",
            "baixa.data_baixa",
        ],
        "data_pagamento",
    )

    for col in ["data_pagamento", "criado_em", "atualizado_em"]:
        if col in out.columns:
            out[col] = safe_to_datetime(out[col])

    out = coalesce_columns(
        out,
        ["valor_composicao.valor_bruto", "valor_bruto", "valor"],
        "valor_baixa",
    )

    out["valor_baixa"] = _safe_numeric_series(out, "valor_baixa")
    if "tipo_evento_financeiro" in out.columns:
        out["tipo_evento_financeiro"] = out["tipo_evento_financeiro"].astype(str).str.upper()
        sign = out["tipo_evento_financeiro"].map({"RECEITA": 1, "DESPESA": -1}).fillna(1)
    else:
        sign = pd.Series(1, index=out.index, dtype="int64")
    out["valor_baixa_sinal"] = out["valor_baixa"] * sign
    return out


def _calendar_from_dataframes(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    dates: list[pd.Series] = []

    for df in dfs:
        for col in ["data_vencimento", "data_competencia", "data_pagamento", "data_fluxo"]:
            if col in df.columns:
                dates.append(pd.to_datetime(df[col], errors="coerce"))

    if not dates:
        return pd.DataFrame()

    all_dates = pd.concat(dates, ignore_index=True).dropna()
    if all_dates.empty:
        return pd.DataFrame()

    calendar = pd.DataFrame(
        {"data": pd.date_range(all_dates.min().normalize(), all_dates.max().normalize(), freq="D")}
    )
    calendar["ano"] = calendar["data"].dt.year
    calendar["mes"] = calendar["data"].dt.month
    calendar["ano_mes"] = calendar["data"].dt.strftime("%Y-%m")
    calendar["trimestre"] = calendar["data"].dt.quarter
    calendar["semana_ano"] = calendar["data"].dt.isocalendar().week.astype(int)
    calendar["dia"] = calendar["data"].dt.day
    calendar["dia_semana"] = calendar["data"].dt.day_name()
    return calendar


def _month_start(value: pd.Timestamp | None) -> pd.Timestamp | pd.NaT:
    if pd.isna(value):
        return pd.NaT
    timestamp = pd.Timestamp(value)
    return timestamp.to_period("M").to_timestamp()


def _next_month_start(value: pd.Timestamp | None) -> pd.Timestamp | pd.NaT:
    month_start = _month_start(value)
    if pd.isna(month_start):
        return pd.NaT
    return month_start + pd.offsets.MonthBegin(1)


def _round_money(value: object) -> float:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return 0.0
    return round(float(numeric), 2)


def _enrich_contracts_with_sales_metrics(contracts: pd.DataFrame, sales: pd.DataFrame) -> pd.DataFrame:
    if contracts.empty:
        return contracts.copy()

    out = contracts.copy()
    out["data_inicio"] = safe_to_datetime(out.get("data_inicio"))
    out["proximo_vencimento"] = safe_to_datetime(out.get("proximo_vencimento"))

    default_columns = {
        "quantidade_vendas_vinculadas": 0,
        "meses_faturados": 0,
        "possui_historico_recorrente": False,
        "primeira_venda": pd.NaT,
        "ultima_venda": pd.NaT,
        "competencia_ultima_venda": pd.NaT,
        "valor_ultima_venda": 0.0,
        "valor_medio_ultimos_3_meses": 0.0,
        "valor_base_renovacao": 0.0,
        "valor_base_renovacao_num": 0.0,
        "possui_venda_apos_vencimento": False,
        "competencia_inicio_renovacao": pd.NaT,
        "competencia_fim_renovacao": pd.NaT,
        "meses_renovacao_automatica": 12,
        "elegivel_renovacao_sem_churn": False,
    }

    for column_name, default_value in default_columns.items():
        out[column_name] = default_value

    if sales.empty or "id_contrato" not in sales.columns:
        return out

    sales_with_contract = sales[sales["id_contrato"].notna()].copy()
    if sales_with_contract.empty:
        return out

    sales_with_contract["id_contrato"] = sales_with_contract["id_contrato"].astype(str)
    sales_with_contract["data"] = safe_to_datetime(sales_with_contract.get("data"))
    sales_with_contract["total"] = pd.to_numeric(sales_with_contract.get("total"), errors="coerce").fillna(0)

    metrics_rows: list[dict[str, object]] = []

    for contract_id, group in sales_with_contract.groupby("id_contrato", dropna=True):
        ordered_group = group.sort_values("data").copy()
        ordered_group = ordered_group[ordered_group["data"].notna()]
        if ordered_group.empty:
            continue

        monthly_totals = (
            ordered_group.groupby(pd.Grouper(key="data", freq="MS"))["total"].sum().sort_index().dropna()
        )
        if monthly_totals.empty:
            continue

        last_sale_row = ordered_group.iloc[-1]
        last_month = monthly_totals.index.max()
        metrics_rows.append(
            {
                "id": contract_id,
                "quantidade_vendas_vinculadas": int(len(ordered_group)),
                "meses_faturados": int(len(monthly_totals)),
                "possui_historico_recorrente": bool(len(ordered_group) >= 4),
                "primeira_venda": ordered_group["data"].min(),
                "ultima_venda": ordered_group["data"].max(),
                "competencia_ultima_venda": last_month,
                "valor_ultima_venda": _round_money(last_sale_row["total"]),
                "valor_medio_ultimos_3_meses": _round_money(monthly_totals.tail(3).mean()),
                "valor_base_renovacao": _round_money(monthly_totals.tail(3).mean()),
                "valor_base_renovacao_num": _round_money(monthly_totals.tail(3).mean()),
            }
        )

    if not metrics_rows:
        return out

    metrics = pd.DataFrame(metrics_rows)
    out = out.merge(metrics, on="id", how="left", suffixes=("", "_metric"))

    for column_name in default_columns:
        metric_column = f"{column_name}_metric"
        if metric_column in out.columns:
            out[column_name] = out[column_name].where(out[metric_column].isna(), out[metric_column])
            out = out.drop(columns=[metric_column])

    out["possui_venda_apos_vencimento"] = (
        out["proximo_vencimento"].notna()
        & out["ultima_venda"].notna()
        & (out["ultima_venda"] > out["proximo_vencimento"])
    )

    renewal_reference = out["competencia_ultima_venda"].where(
        out["competencia_ultima_venda"].notna(),
        out["proximo_vencimento"],
    )
    out["competencia_inicio_renovacao"] = renewal_reference.apply(_next_month_start)
    out["competencia_fim_renovacao"] = out["competencia_inicio_renovacao"].where(
        out["competencia_inicio_renovacao"].isna(),
        out["competencia_inicio_renovacao"] + pd.DateOffset(months=11),
    )

    out["elegivel_renovacao_sem_churn"] = (
        out.get("status", pd.Series(index=out.index, dtype="object")).astype(str).str.upper().eq("ATIVO")
        & out["possui_historico_recorrente"]
        & out["valor_base_renovacao"].gt(0)
        & out["competencia_inicio_renovacao"].notna()
        & out["competencia_fim_renovacao"].notna()
    )

    out["quantidade_vendas_vinculadas"] = pd.to_numeric(
        out["quantidade_vendas_vinculadas"], errors="coerce"
    ).fillna(0).astype("Int64")
    out["meses_faturados"] = pd.to_numeric(out["meses_faturados"], errors="coerce").fillna(0).astype("Int64")
    out["meses_renovacao_automatica"] = pd.to_numeric(
        out["meses_renovacao_automatica"], errors="coerce"
    ).fillna(12).astype("Int64")
    for column_name in [
        "valor_ultima_venda",
        "valor_medio_ultimos_3_meses",
        "valor_base_renovacao",
        "valor_base_renovacao_num",
    ]:
        out[column_name] = pd.to_numeric(out[column_name], errors="coerce").fillna(0.0).round(2)

    for column_name in [
        "possui_historico_recorrente",
        "possui_venda_apos_vencimento",
        "elegivel_renovacao_sem_churn",
    ]:
        out[column_name] = out[column_name].fillna(False).astype(bool)

    for column_name in [
        "data_inicio",
        "proximo_vencimento",
        "primeira_venda",
        "ultima_venda",
        "competencia_ultima_venda",
        "competencia_inicio_renovacao",
        "competencia_fim_renovacao",
    ]:
        out[column_name] = safe_to_datetime(out[column_name]).dt.normalize()

    return out


def build_analytics(raw: dict[str, pd.DataFrame]) -> AnalyticsTables:
    categories = raw.get("categorias", pd.DataFrame()).copy()
    dre = raw.get("categorias_dre", pd.DataFrame()).copy()
    categories = _enrich_categories_with_dre(categories, dre)
    cost_centers = raw.get("centros_custo", pd.DataFrame()).copy()
    accounts = raw.get("contas_financeiras", pd.DataFrame()).copy()
    balances = raw.get("saldos_contas_financeiras", pd.DataFrame()).copy()
    people = raw.get("pessoas", pd.DataFrame()).copy()

    receivable = _prepare_financial_events(raw.get("contas_receber", pd.DataFrame()), event_kind="RECEITA")
    payable = _prepare_financial_events(raw.get("contas_pagar", pd.DataFrame()), event_kind="DESPESA")
    transfers = raw.get("transferencias", pd.DataFrame()).copy()
    sales = raw.get("vendas", pd.DataFrame()).copy()
    contracts = raw.get("contratos", pd.DataFrame()).copy()
    acquittances = _prepare_acquittances(raw.get("baixas", pd.DataFrame()).copy())

    financial_events = (
        pd.concat([receivable, payable], ignore_index=True)
        if (not receivable.empty or not payable.empty)
        else pd.DataFrame()
    )
    contracts = _enrich_contracts_with_sales_metrics(contracts, sales)
    acquittances = _enrich_acquittances_with_origin(acquittances, financial_events)

    # fluxo_realizado derivado das baixas já enriquecidas com metadados de origem
    fluxo_realizado = acquittances.copy()
    if not fluxo_realizado.empty and "data_pagamento" in fluxo_realizado.columns:
        fluxo_realizado["data_fluxo"] = fluxo_realizado["data_pagamento"].dt.normalize()
        fluxo_realizado["valor_fluxo"] = fluxo_realizado.get("valor_baixa_sinal", fluxo_realizado["valor_baixa"])

    fluxo_compromissado = financial_events.copy()
    if not fluxo_compromissado.empty and "data_vencimento" in fluxo_compromissado.columns:
        fluxo_compromissado["data_fluxo"] = fluxo_compromissado["data_vencimento"].dt.normalize()
        fluxo_compromissado["valor_fluxo"] = fluxo_compromissado["valor_documento_sinal"]

    consolidado = financial_events.copy()
    if not consolidado.empty:
        if "data_competencia" in consolidado.columns:
            consolidado["ano_mes_competencia"] = consolidado["data_competencia"].dt.strftime("%Y-%m")
        if "data_vencimento" in consolidado.columns:
            consolidado["ano_mes_vencimento"] = consolidado["data_vencimento"].dt.strftime("%Y-%m")

    calendar = _calendar_from_dataframes([receivable, payable, acquittances, fluxo_realizado, fluxo_compromissado])

    tables = {
        "dim_categoria": categories,
        "dim_categoria_dre": dre,
        "dim_centro_custo": cost_centers,
        "dim_conta_financeira": accounts,
        "dim_pessoa": people,
        "fato_saldos_contas": balances,
        "fato_contas_a_receber": receivable,
        "fato_contas_a_pagar": payable,
        "fato_transferencias": transfers,
        "fato_vendas": sales,
        "fato_contratos": contracts,
        "fato_baixas": acquittances,
        "fato_fluxo_caixa_realizado": fluxo_realizado,
        "fato_fluxo_caixa_compromissado": fluxo_compromissado,
        "fato_financeiro_consolidado": consolidado,
        "dim_calendario": calendar,
    }

    return AnalyticsTables(tables=tables)
