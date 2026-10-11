"""Constantes do marketplace monitorado (módulo folha: sem dependências do coletor)."""

from __future__ import annotations

import re

MARKETPLACE = "mercadolivre"
# Tipo de produto do catálogo do ML ("MLB-AIR_COMPRESSORS"). Entra em caminho de URL da API.
DOMAIN_ID = re.compile(r"^[A-Z]{3}-[A-Z0-9_]{1,70}$")
