rem uv run autoflake -i -r ai.py circle_provider.py gemini.py models.py secret_key.py ai_client.py editor.py graphics.py main.py scenes.py utils.py
rem uv run isort           ai.py circle_provider.py gemini.py models.py secret_key.py ai_client.py editor.py graphics.py main.py scenes.py utils.py
rem uv run black           ai.py circle_provider.py gemini.py models.py secret_key.py ai_client.py editor.py graphics.py main.py scenes.py utils.py
uv run ruff format     ai.py circle_provider.py gemini.py models.py secret_key.py ai_client.py editor.py graphics.py main.py scenes.py utils.py
