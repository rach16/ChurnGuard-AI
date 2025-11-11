# Customer Churn Prediction & Analysis System

<!-- Project Identity & Certification -->
![RAG](https://img.shields.io/badge/RAG-Customer_Churn_Analysis-FF6B6B?style=for-the-badge&logo=openai&logoColor=white)
![AIE8 Certified](https://img.shields.io/badge/AIE8-Certified_Challenge-gold?style=for-the-badge&logo=certificate&logoColor=white)
![Customer Analytics](https://img.shields.io/badge/Domain-Customer_Analytics-2563eb?style=for-the-badge&logo=analytics&logoColor=white)

<!-- Core Tech Stack -->
![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5.1+-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14.2+-000000?style=for-the-badge&logo=next.js&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Orchestration_Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)

<!-- AI/ML/LLM Stack -->
![LangChain](https://img.shields.io/badge/🦜_LangChain-0.3+-1C3C3C?style=for-the-badge&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agents-FF6B35?style=for-the-badge&logo=graphql&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4-412991?style=for-the-badge&logo=openai&logoColor=white)
![Qdrant](https://img.shields.io/badge/Qdrant-Vector_DB-DC382D?style=for-the-badge&logo=database&logoColor=white)
![RAGAS](https://img.shields.io/badge/RAGAS-Evaluation-FF9500?style=for-the-badge&logo=chartdotjs&logoColor=white)

---

**AIE8 Certification Challenge** - Advanced RAG system for customer churn assessment and prediction

An intelligent assistant that analyzes customer data, historical churn patterns, and business documentation to provide actionable insights on customer retention, risk assessment, and personalized intervention strategies.

## 🚀 **Complete Docker Orchestration**
**Get the entire RAG system running with a single command!** All services (Vector DB + Backend API + Jupyter + Frontend) are fully containerized with automated service management, health checks, and persistent volumes.

---

## 📖 Table of Contents

- [🚀 Quick Start](#-quick-start)
  - [1. Environment Setup](#1-environment-setup)
  - [2. Start All Services with Docker](#2-start-all-services-with-docker)
  - [3. Open & Use the Assistant](#3-open--use-the-assistant)
- [✨ Core Features](#-core-features)
- [📁 Project Structure](#-project-structure)
- [🔗 API Usage](#-api-usage)
- [🛠 Development](#-development)
- [📋 Requirements](#-requirements)

## 🚀 Quick Start

Get the entire RAG system running in 3 simple steps:

### 1. Environment Setup
```bash
# Copy environment template and add your API keys
cp .env-example .env

# Edit .env file with your required API keys:
# OPENAI_API_KEY=your_key_here (Required)
# TAVILY_API_KEY=your_key_here (Optional - for external search)
# LANGCHAIN_API_KEY=your_key_here (Optional - for tracing)
```

### 2. Start All Services with Docker
```bash
# 🚀 Interactive Menu (Default) - Choose your startup mode
./start-services.sh

# The script presents 4 startup options:
# 1. 🚀 Full startup (recommended)
#    • Stops existing containers
#    • Cleans up: dangling images, build cache
#    • Rebuilds and starts all services
#    • Includes: Backend + Jupyter + Frontend + Qdrant
#
# 2. ⚡ Quick restart (development)
#    • Skips Docker cleanup (faster)
#    • Rebuilds and starts all services
#    • Best for active development
#
# 3. 🔬 Backend + Jupyter only
#    • Skips frontend service
#    • Ideal for notebook experiments
#
# 4. 🎯 Custom configuration
#    • Choose individual options interactively

# ⚡ Non-Interactive Mode (for automation/scripts)
./start-services.sh --mode=full              # Full startup with cleanup
./start-services.sh --mode=quick             # Quick restart (no cleanup)
./start-services.sh --mode=backend           # Backend + Jupyter only

# Alternative: Manual Docker Compose
docker compose up --build -d
```

**🎉 Single Command Deployment!** All services start automatically with:
- ✅ **Service Dependencies** - Proper startup ordering
- ✅ **Health Checks** - Automated service validation
- ✅ **Data Persistence** - Volumes for cache and data
- ✅ **Network Isolation** - Dedicated Docker network
- ✅ **Multi-stage Builds** - Optimized container images

**Services Available:**
- **📊 Qdrant Vector Database**: http://localhost:6333/dashboard
- **🤖 Backend RAG API**: http://localhost:8000
- **📚 Jupyter Lab**: http://localhost:8888
- **📖 API Documentation**: http://localhost:8000/docs
- **🎨 Frontend Dashboard**: http://localhost:3000

### ⏹️ Stop All Services
```bash
# 🛑 Interactive Menu (Default)
./stop-services.sh

# The script presents 4 stop options:
# 1. 🛑 Standard stop (recommended)
# 2. ⏸️  Quick pause (fastest restart)
# 3. 🔧 Deep cleanup (reclaim disk space)
# 4. 💣 Nuclear reset (⚠️  DATA LOSS WARNING)

# ⚡ Non-Interactive Mode
./stop-services.sh --mode=standard
./stop-services.sh --mode=quick
./stop-services.sh --mode=deep
./stop-services.sh --mode=nuclear            # ⚠️ DELETES ALL DATA
```

### 3. Open & Use the System
Once all services are running, you can access:

- **🎨 Frontend Dashboard**: http://localhost:3000 - Interactive churn analysis interface
- **📚 Jupyter Notebooks**: http://localhost:8888 - RAG experiments and model evaluation
- **📖 API Documentation**: http://localhost:8000/docs - REST API endpoints
- **📊 Qdrant Dashboard**: http://localhost:6333/dashboard - Vector database monitoring

**📝 Testing Guide**: For complete testing instructions, troubleshooting, and validation steps, see [`docs/E2E_TESTING.md`](docs/E2E_TESTING.md)

## ✨ Core Features

### 🎯 **AI-Powered Customer Churn Analysis**
- **Churn Risk Assessment** - Analyze customer behavior patterns and predict churn probability
- **Personalized Retention Strategies** - Generate targeted intervention recommendations
- **Historical Pattern Analysis** - Learn from past churn cases and successful retention efforts
- **Multi-Data Source Integration** - Combine structured customer data with unstructured documents

### 🔍 **Advanced RAG Architecture**
- **Hybrid Dataset** - Customer records + business policies + churn reports
- **5 Retrieval Strategies** - Naive, Multi-Query, Parent-Document (⭐ 95.6% faithfulness), Contextual Compression, Reranking
- **Multi-Agent System** - Research Team + Writing Team (5 sub-agents) for comprehensive analysis
- **Agent Orchestration** - LangGraph-based tool selection with Tavily Search integration
- **Performance Evaluation** - RAGAS + SDG for comprehensive baseline metrics

### 🚀 **Production-Ready Deployment**
- **Complete Docker Orchestration** - Multi-service containerization with health checks
- **One-Command Deployment** - Automated service management and startup
- **Real-time Monitoring** - Comprehensive logging and performance tracking
- **Scalable Architecture** - Horizontal scaling support with load balancing

### 💬 **Enhanced User Experience**
- **Interactive Dashboard** - Clean, responsive web interface for churn analysis
- **Customer Segmentation** - Role-based analysis (High-value, At-risk, Loyal customers)
- **Performance Metrics** - Real-time response times, prediction confidence scores
- **RESTful API** - Production-ready endpoints for churn prediction and analysis

## 📁 Project Structure

```
├── 📁 Core Application
│   ├── src/backend/              # FastAPI server with RAG endpoints
│   ├── src/core/                 # RAG retrieval implementations (5 strategies)
│   ├── src/agents/               # Multi-agent system with LangGraph
│   │   ├── churn_agent.py        #   • Single-agent orchestration
│   │   ├── multi_agent_system.py #   • Multi-agent coordinator
│   │   ├── research_team.py      #   • Research Team (context gathering)
│   │   └── writing_team.py       #   • Writing Team (5 sub-agents)
│   ├── src/evaluation/           # RAGAS evaluation and SDG
│   ├── src/tools/                # External API tools (Tavily search)
│   ├── src/utils/                # Utilities and helper functions
│   └── src/visualization/        # Performance visualization tools
├── 🎨 Frontend
│   └── frontend/                 # Next.js dashboard interface
├── 📊 Data & Analysis
│   ├── data/                     # Customer data, churn reports, policies
│   ├── notebooks/                # Jupyter research & evaluation
│   ├── golden-masters/           # Generated test datasets (SDG)
│   └── metrics/                  # RAGAS evaluation results
├── 🐳 Docker Infrastructure
│   ├── docker-compose.yml        # Multi-service orchestration
│   ├── start-services.sh         # Automated deployment script
│   ├── stop-services.sh          # Graceful shutdown script
│   └── setup.sh                  # Development setup utilities
├── 📚 Documentation
│   ├── docs/                     # Project documentation
│   ├── README.md                 # Main project documentation
│   └── screenshots/              # Application screenshots
└── ⚙️ Configuration
    ├── .env-example              # Environment variables template
    ├── pyproject.toml            # Python dependencies
    └── .gitignore                # Git ignore patterns
```

## 🔗 API Usage

**POST** `/multi-agent-analyze` ⭐ - Multi-agent comprehensive analysis
```json
{
  "query": "Why are customers churning?",
  "use_research_team": true,
  "use_writing_team": true
}
```

**POST** `/analyze-churn` - Single agent churn risk analysis
```json
{
  "customer_id": "CUST_12345",
  "query": "What is the churn risk for this customer?",
  "include_recommendations": true
}
```

**POST** `/ask` - Simple RAG Q&A with selectable retrieval strategy
```json
{
  "question": "What are the common patterns in high-value customer churn?",
  "retriever_type": "parent_document",
  "max_response_length": 2000
}
```

**Response includes:**
- Generated answer with contextual sources and relevance scores
- Comprehensive performance metrics (response time, tokens used, confidence)
- Source document transparency with relevance scoring
- Multi-agent processing stages and quality notes (for multi-agent mode)
- Tool usage tracking and agent decision logs

## 🛠 Development

This project implements cutting-edge RAG techniques with comprehensive evaluation:

### 🧪 **Advanced RAG Research**
- **Hybrid Dataset**: Customer records + business policies + churn analysis reports
- **Multiple Retrievers**: Naive, Multi-Query, Parent-Document, Contextual Compression
- **Agent Framework**: LangGraph with StateGraph orchestration and external API tools
- **Evaluation Pipeline**: RAGAS metrics + Synthetic Data Generation (SDG) for baselining

### 📊 **Performance Baselining**
- **SDG (Synthetic Data Generation)**: Generate diverse test questions for evaluation
- **RAGAS Evaluation**: Comprehensive metrics (Context Recall, Precision, Faithfulness, etc.)
- **Retrieval Ranking**: Benchmark all retrieval methods
- **Golden Master Datasets**: Cached evaluation datasets to avoid regeneration

### 🔬 **Research Notebooks**
- **Churn Analysis Experiments**: Main notebook for RAG-based churn prediction
- **Retriever Comparison**: Traditional retrieval method benchmarking
- **SDG & RAGAS Baseline**: Synthetic data generation and evaluation
- **Performance Visualization**: Heatmap and metric analysis tools

## 📋 Requirements

### System Requirements
- **Docker** (20.10+) - For containerized deployment
- **Docker Compose** (2.0+) - For multi-container orchestration
- **Git** (2.25+) - For cloning the repository
- **Modern Browser** - Chrome, Firefox, Safari, or Edge
- **Memory**: 4GB+ RAM (8GB+ recommended)
- **Storage**: 3GB+ free space

### API Keys (Required)
Create a `.env` file in the project root with:
```bash
# Required API Keys
OPENAI_API_KEY=your_openai_key_here          # For LLM and embeddings

# Optional API Keys
TAVILY_API_KEY=your_tavily_key_here          # For external search (optional)
LANGCHAIN_API_KEY=your_langsmith_key_here    # For tracing/monitoring (optional)
```

### Port Usage
| Service | Port | URL | Purpose |
|---------|------|-----|----------|
| **Qdrant** | 6333 | http://localhost:6333/dashboard | Vector database dashboard |
| **Backend API** | 8000 | http://localhost:8000 | RAG API endpoints |
| **API Documentation** | 8000 | http://localhost:8000/docs | OpenAPI/Swagger docs |
| **Jupyter Lab** | 8888 | http://localhost:8888 | Notebook environment |
| **Frontend** | 3000 | http://localhost:3000 | Web interface |

### Data Requirements

**📊 Expected Dataset Structure:**
```
data/
├── customer_churn_data.csv       # Customer records with churn labels
├── retention_policies.pdf        # Business retention policies
├── churn_analysis_report.pdf     # Historical churn analysis
└── customer_feedback.txt         # Unstructured customer feedback
```

**🎯 Data Processing Pipeline:**
- **CSV Data**: Customer records, transaction history, engagement metrics
- **PDF Documents**: Business policies, historical reports, analysis documents
- **Text Files**: Customer feedback, support tickets, communication logs
- **Combined Dataset**: Unified vector embeddings in Qdrant

---

**Ready to transform customer retention with AI-powered churn analysis!** 📊

---

## 📊 Performance & Metrics

<!-- System Performance Metrics -->
![Response Time](https://img.shields.io/badge/Response_Time-2--5s-blue?style=for-the-badge&logo=stopwatch&logoColor=white)
![Accuracy](https://img.shields.io/badge/Churn_Prediction-85%25+-brightgreen?style=for-the-badge&logo=target&logoColor=white)
![Dataset Size](https://img.shields.io/badge/Dataset-Dynamic-orange?style=for-the-badge&logo=database&logoColor=white)

<!-- License & Social -->
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)

---

## 🎯 Next Steps

1. **Prepare Your Data**: Place customer churn data in the `data/` folder
2. **Configure Environment**: Update `.env` with your API keys
3. **Start Services**: Run `./start-services.sh` to launch the system
4. **Explore Notebooks**: Use Jupyter to experiment with RAG approaches
5. **Evaluate Performance**: Run RAGAS + SDG baselines in notebooks
6. **Deploy Frontend**: Access the dashboard to interact with the system
