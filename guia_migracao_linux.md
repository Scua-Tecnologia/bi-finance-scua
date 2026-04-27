# Guia de Migração: Servidor Linux (Docker nativo) — Scua

Como você já possui um `Dockerfile` e o script de `entrypoint.sh` na base do projeto, o código não tem dependências presas a nuvens específicas. A execução pode ser feita utilizando os recursos nativos do Docker no seu servidor.

## Passo a Passo no Servidor Linux

### 1. Preparar os Arquivos
Acesse seu servidor Linux via SSH e clone o repositório oficial da Scua:
```bash
git clone https://gitlab.com/scuacorp/bi-finance-scua.git
cd bi-finance-scua
```

### 2. Configurar o `.env` e Segredos
1. **Configurar Variáveis de Ambiente:**
```bash
cp .env.example .env
nano .env # Insira as variáveis DATABASE_URL, CONTA_AZUL*, etc.
```

2. **Configurar Credenciais do Dashboard (`secrets.toml`):**
```bash
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
nano .streamlit/secrets.toml # Insira os usuários e hashes bcrypt
```

### 3. Buildar a Imagem
```bash
sudo docker build -t bi-finance-scua .
```

### 4. Rodar o Dashboard (Visualização)
O comando abaixo inicia o dashboard em background, com reinicialização automática:
```bash
sudo docker run -d \
  --name bi-dashboard \
  --restart unless-stopped \
  -p 8501:8501 \
  --env-file .env \
  -e STREAMLIT_SECRETS="$(base64 -w0 .streamlit/secrets.toml)" \
  bi-finance-scua
```

### 5. Configurar o ETL (Atualização de Dados)
Como o projeto não está mais no GitHub, o agendamento diário deve ser feito no **Crontab** do seu servidor para rodar o script de extração dentro do container:

1. Abra o editor de agendamento: `crontab -e`
2. Adicione a linha abaixo para rodar o ETL todo dia às 10:00:
```bash
00 10 * * * docker exec bi-dashboard python -m contaazul_bi.main run
```

---

### Cheatsheet de Manutenção:

- **Ver logs:** `sudo docker logs -f bi-dashboard`
- **Executar ETL manualmente agora:** `sudo docker exec bi-dashboard python -m contaazul_bi.main run`
- **Atualizar o sistema (após novos commits no GitLab):**
  ```bash
  git pull
  sudo docker build -t bi-finance-scua .
  sudo docker rm -f bi-dashboard
  # Execute o comando do Passo 4 novamente
  ```
