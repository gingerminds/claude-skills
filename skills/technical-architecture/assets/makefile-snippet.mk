# Architecture technique (docs/architecture/)
ARCHITECTURE_TOOL := python3 docs/architecture/tools/architecture.py

.PHONY: diagrams architecture-pdf

diagrams: ## Régénère les vues d'architecture (draw.io dans Docker) et vérifie vues et matrice des flux
	$(ARCHITECTURE_TOOL) refresh
	$(ARCHITECTURE_TOOL) check

architecture-pdf: ## Génère le dossier PDF dans docs/architecture/build/ (SANS_POINTS_ATTENTION=1 : sans les points d'attention)
	$(ARCHITECTURE_TOOL) pdf $(if $(filter 1 oui yes true,$(SANS_POINTS_ATTENTION)),--sans-points-attention)
