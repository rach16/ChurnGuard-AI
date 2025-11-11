# 🚀 Getting Started - Customer Churn RAG System

Welcome! This guide will help you get started with the Customer Churn RAG system in just a few steps.

## ⚡ Quick Start (5 minutes)

### 1️⃣ Set up environment
```bash
# Copy environment template
cp .env-example .env

# Add your OpenAI API key
echo "OPENAI_API_KEY=sk-your-key-here" >> .env
```

### 2️⃣ Start all services
```bash
# Make scripts executable (if not already)
chmod +x start-services.sh stop-services.sh

# Start everything with one command
./start-services.sh
```

### 3️⃣ Access the system
- **Frontend**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs
- **Jupyter**: http://localhost:8888
- **Qdrant**: http://localhost:6333/dashboard

That's it! 🎉

## 📝 What You Need to Do Next

### Step 1: Add Your Data
Place your customer churn data in the `data/` folder:

```bash
data/
├── customer_churn_data.csv       # Your customer records
├── retention_policies.pdf        # Business policies (optional)
└── churn_analysis_report.pdf     # Historical reports (optional)
```

**Don't have data yet?** Use these public datasets:
- [Kaggle Telco Churn](https://www.kaggle.com/datasets/blastchar/telco-customer-churn)
- [UCI Churn Dataset](https://archive.ics.uci.edu/ml/datasets/Churn+Modelling)

### Step 2: Implement RAG Retrievers
Edit `src/core/rag_retrievers.py` to:
1. Load your documents
2. Create embeddings
3. Store in Qdrant
4. Implement retrieval methods

**Start here**: Open Jupyter at http://localhost:8888
- Go to `notebooks/01_churn_rag_experiments.ipynb`
- Follow the TODO comments

### Step 3: Build Your Agent
Edit `src/agents/churn_agent.py` to:
1. Define agent state
2. Add tools (RAG, search, analysis)
3. Create LangGraph workflow
4. Test agent execution

### Step 4: Connect to API
Edit `src/backend/api.py` to:
1. Import your agent and retrievers
2. Implement `/analyze-churn` endpoint
3. Implement `/ask` endpoint
4. Add metrics tracking

### Step 5: Run Evaluation
In Jupyter notebook:
1. Generate test questions (SDG)
2. Run RAG system on tests
3. Calculate RAGAS metrics
4. Save results to `metrics/` folder

### Step 6: Customize Frontend
Edit `frontend/src/app/page.tsx` to:
1. Add customer segmentation views
2. Display churn risk scores
3. Show recommendations
4. Add charts and visualizations

## 🎯 Project Structure at a Glance

```
Core Implementation Files (you'll edit these):
├── src/core/rag_retrievers.py           ← Implement retrieval strategies
├── src/agents/churn_agent.py            ← Build LangGraph agent
├── src/backend/api.py                   ← Connect agent to API
├── src/evaluation/ragas_evaluation.py   ← Run RAGAS evaluation
├── src/evaluation/synthetic_data_generation.py  ← Generate test questions
└── frontend/src/app/page.tsx            ← Customize UI

Experiment & Test:
├── notebooks/01_churn_rag_experiments.ipynb  ← Start here!
└── data/                                     ← Your data goes here

Configuration (already done):
├── docker-compose.yml                   ✅ Service orchestration
├── pyproject.toml                       ✅ Python dependencies
├── .env-example                         ✅ Environment template
└── start-services.sh / stop-services.sh ✅ Startup scripts
```

## 🔍 Key Features to Implement

### 1. RAG with Multiple Retrievers
Implement these in `src/core/rag_retrievers.py`:
- ✅ Naive retrieval (basic similarity search)
- ✅ Multi-query retrieval (query variations)
- ✅ Contextual compression (focused results)
- ✅ Parent-document retrieval (full context)

### 2. Agent with External Tools
Build in `src/agents/churn_agent.py`:
- ✅ LangGraph state machine
- ✅ RAG retrieval tools
- ✅ External search (Tavily)
- ✅ Churn analysis logic
- ✅ Recommendation generation

### 3. RAGAS + SDG Baselining
Implement in `src/evaluation/`:
- ✅ Generate synthetic test questions (SDG)
- ✅ Run RAGAS evaluation metrics
- ✅ Compare retrieval methods
- ✅ Visualize results with heatmaps

### 4. Full-Stack App
Connect everything:
- ✅ FastAPI backend with endpoints
- ✅ Next.js frontend dashboard
- ✅ Docker orchestration
- ✅ Health checks and monitoring

## 📚 Helpful Commands

### Service Management
```bash
# Start services (interactive menu)
./start-services.sh

# Stop services (interactive menu)
./stop-services.sh

# Quick restart (development)
./start-services.sh --mode=quick

# View logs
docker compose logs -f backend
docker compose logs -f jupyter
```

### Testing the API
```bash
# Health check
curl http://localhost:8000/health

# Ask a question
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What causes customer churn?"}'
```

### Python Development
```bash
# Install dependencies locally (optional)
pip install uv
uv sync

# Run tests
uv run pytest

# Format code
uv run black src/
```

## 🐛 Troubleshooting

### Services won't start?
```bash
# Check Docker is running
docker ps

# Check logs
docker compose logs backend

# Restart clean
./stop-services.sh --mode=deep
./start-services.sh --mode=full
```

### Import errors in Jupyter?
```python
# In notebook, add to first cell:
import sys
from pathlib import Path
sys.path.append(str(Path.cwd().parent / 'src'))
```

### Missing API keys?
```bash
# Check .env file exists
cat .env | grep OPENAI_API_KEY

# If missing, add it
echo "OPENAI_API_KEY=sk-your-key" >> .env
```

## 🎓 Learning Path

### Week 1: Foundation
- [ ] Set up environment and start services
- [ ] Load data and create embeddings
- [ ] Implement basic RAG retrieval
- [ ] Test in Jupyter notebook

### Week 2: Advanced RAG
- [ ] Implement all retrieval strategies
- [ ] Compare retrieval methods
- [ ] Build LangGraph agent
- [ ] Add external tools

### Week 3: Evaluation
- [ ] Generate test questions (SDG)
- [ ] Run RAGAS evaluation
- [ ] Visualize metrics
- [ ] Optimize based on results

### Week 4: Full-Stack
- [ ] Connect agent to API
- [ ] Customize frontend
- [ ] End-to-end testing
- [ ] Create demo and documentation

## 📖 Documentation

- **Setup Guide**: `docs/SETUP_GUIDE.md` - Detailed setup instructions
- **Project Overview**: `docs/PROJECT_OVERVIEW.md` - Architecture and design
- **Data Guide**: `data/README.md` - Data requirements and formats
- **Main README**: `README.md` - Project introduction

## 💡 Tips for Success

1. **Start with Jupyter** - Use notebooks to experiment before coding
2. **Test incrementally** - Test each component as you build it
3. **Use the logs** - `docker compose logs -f` is your friend
4. **Read the TODOs** - All files have TODO comments showing what to implement
5. **Check examples** - Look at the AIE7-Cert-Challenge for inspiration

## 🆘 Need Help?

1. Check the logs: `docker compose logs -f`
2. Review documentation in `docs/`
3. Test API at http://localhost:8000/docs
4. Verify data loading in Jupyter

## 🎉 Next Steps

1. ✅ **You've got the skeleton** - All infrastructure is ready
2. 👉 **Start with data** - Add your customer churn data to `data/`
3. 👉 **Open Jupyter** - http://localhost:8888 and start experimenting
4. 👉 **Build incrementally** - RAG → Agent → Evaluation → Frontend

**You're ready to build!** 🚀

---

Good luck with your AIE8 certification challenge! 🎓

