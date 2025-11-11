# End-to-End Testing Guide

Complete guide for testing the Customer Churn RAG system with Docker Compose.

---

## 🚀 **Quick Start (3 Steps)**

```bash
# 1. Set your API key
echo 'OPENAI_API_KEY=your-key-here' > .env

# 2. Start everything
docker compose up --build -d

# 3. Test it
./test-e2e.sh
```

**Then open:** http://localhost:3000

---

## 📋 **Table of Contents**

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start-3-steps)
3. [Service Overview](#service-overview)
4. [Testing Checklist](#testing-checklist)
5. [Troubleshooting](#troubleshooting)
6. [Common Commands](#common-commands)
7. [Success Criteria](#success-criteria)

---

## 📋 **Prerequisites**

### **Required Software**
```bash
docker --version          # Docker 20.10+
docker compose version    # Docker Compose 2.0+
```

### **Required API Keys**

Create a `.env` file in the project root:

```bash
# Required
OPENAI_API_KEY=sk-proj-your-key-here

# Optional (recommended)
COHERE_API_KEY=your-cohere-key-here

# Optional
TAVILY_API_KEY=your-tavily-key-here
LANGCHAIN_API_KEY=your-langsmith-key-here
LANGCHAIN_TRACING_V2=false
```

---

## 🏗️ **Service Overview**

### **What Gets Started**

| Service | URL | What It Does | Health Check |
|---------|-----|-------------|--------------|
| **Frontend** | http://localhost:3000 | Beautiful UI for asking questions | `curl http://localhost:3000` |
| **Backend** | http://localhost:8000 | RAG system with GPT-4o-mini | `curl http://localhost:8000/health` |
| **Backend API Docs** | http://localhost:8000/docs | Swagger UI | N/A |
| **Qdrant** | http://localhost:6333 | Vector database (68 customer records) | `curl http://localhost:6333/healthz` |
| **Qdrant Dashboard** | http://localhost:6333/dashboard | Vector DB management UI | N/A |
| **Jupyter Lab** | http://localhost:8888 | Optional notebooks | N/A |

### **Architecture**

```
┌─────────────┐
│   Frontend  │ ──── HTTP ───┐
│ (Next.js)   │              │
└─────────────┘              ▼
                      ┌──────────────┐
                      │   Backend    │
                      │  (FastAPI)   │
                      └──────────────┘
                             │
                    ┌────────┴────────┐
                    ▼                 ▼
            ┌──────────────┐   ┌──────────┐
            │    Qdrant    │   │  OpenAI  │
            │ (Vector DB)  │   │   API    │
            └──────────────┘   └──────────┘
```

---

## 🚀 **Detailed Setup Steps**

### **Step 1: Start All Services**

```bash
# Navigate to project root
cd /Users/rachanabanik/Desktop/AICERT/AIE8-Cert-Challenge

# Build and start all services
docker compose up --build -d

# Or use the convenience script
./start-services.sh
```

**Expected Output:**
```
[+] Building 45.2s (23/23) FINISHED
[+] Running 5/5
 ✔ Network churn-network      Created
 ✔ Volume "qdrant_storage"    Created
 ✔ Container churn-qdrant     Started (healthy)
 ✔ Container churn-backend    Started (healthy)
 ✔ Container churn-frontend   Started (healthy)
 ✔ Container churn-jupyter    Started
```

---

### **Step 2: Monitor Startup**

Watch the logs to see services initializing:

```bash
# Watch all logs
docker compose logs -f

# Or watch specific service
docker compose logs -f backend

# Press Ctrl+C to stop watching
```

**What to look for in Backend logs:**
```
🚀 Initializing RAG system...
📊 Loading RAG retriever...
✓ Loaded and indexed 68 documents
✓ Loaded knowledge graph from cache
🤖 Initializing churn agent...
✓ Churn agent initialized
✅ RAG system fully initialized and ready!
INFO:     Uvicorn running on http://0.0.0.0:8000
```

⏱️ **Expected startup time:** 30-60 seconds

---

### **Step 3: Check Service Health**

```bash
# Check all service status
docker compose ps

# Expected output:
# NAME              IMAGE             STATUS
# churn-qdrant      qdrant/qdrant     Up (healthy)
# churn-backend     churn-backend     Up (healthy)
# churn-frontend    churn-frontend    Up (healthy)
# churn-jupyter     churn-jupyter     Up

# Test individual endpoints
curl http://localhost:6333/healthz         # Qdrant health
curl http://localhost:8000/health          # Backend health
curl http://localhost:3000                 # Frontend (HTML response)
```

---

## 🧪 **Testing Checklist**

### **Test 1: Qdrant Vector Database** ✅

```bash
# Check Qdrant is running
curl -s http://localhost:6333/healthz | jq

# Expected: {"title":"healthz OK","status":200}

# Check collections (after backend starts)
curl -s http://localhost:6333/collections | jq

# Open Qdrant Dashboard
open http://localhost:6333/dashboard
```

**✅ Success Criteria:**
- Dashboard loads
- Collection `customer_churn` exists
- Shows 68+ vectors indexed

---

### **Test 2: Backend API** ✅

```bash
# Test health endpoint
curl -s http://localhost:8000/health | jq

# Expected output:
# {
#   "status": "healthy",
#   "timestamp": "2024-01-01T12:00:00Z",
#   "service": "customer-churn-rag-api"
# }

# Test /ask endpoint with a real question
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Why do customers churn?",
    "max_response_length": 2000
  }' | jq

# Expected: JSON with answer, sources, and metrics

# Open API documentation
open http://localhost:8000/docs
```

**✅ Success Criteria:**
- Health endpoint returns 200
- `/ask` returns answer with 5 sources
- API docs load in Swagger UI
- Response time < 5 seconds
- Method shows "parent_document"

---

### **Test 3: Frontend Dashboard** ✅

```bash
# Check frontend is running
curl -s -I http://localhost:3000 | head -1

# Expected: HTTP/1.1 200 OK

# Open frontend in browser
open http://localhost:3000
```

**Manual Testing Steps:**

1. **Backend Status Check**
   - ✅ Top-right corner shows "Backend Online" with green dot
   
2. **Submit a Query**
   - Enter: "Why do customers churn?"
   - Click "🔍 Analyze with RAG"
   - ✅ Button shows loading spinner
   - ✅ Response appears in 2-5 seconds
   
3. **Verify Response Quality**
   - ✅ Answer is displayed in blue gradient box
   - ✅ Sources section shows 5 documents
   - ✅ Each source has relevance score
   - ✅ Metrics show response time, tokens, method, documents
   
4. **Test Different Questions**
   ```
   • What are the main churn reasons in the Commercial segment?
   • Which competitors are we losing customers to?
   • What is the average customer tenure?
   • Show me high-value churned customers
   • What retention strategies should we focus on?
   ```
   
5. **Error Handling Test**
   - Stop backend: `docker compose stop backend`
   - Try submitting a query
   - ✅ Status changes to "Backend Offline" with red dot
   - ✅ Error message appears
   - Restart: `docker compose start backend`

---

### **Test 4: End-to-End Flow** ✅

Complete user journey test:

```bash
# 1. User opens frontend
open http://localhost:3000

# 2. Frontend checks backend health (automatic on page load)
# 3. User asks question (manual in browser)
# 4. Frontend calls backend API (check browser Network tab)
# 5. Backend retrieves from Qdrant (check logs)
# 6. Backend generates answer with LLM (watch OpenAI API calls)
# 7. Response displayed to user (check frontend)
```

**Monitor the flow:**
```bash
# Watch backend logs in real-time
docker compose logs -f backend

# In another terminal, submit a query
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Why do customers churn?"}' | jq
```

**✅ Success Criteria:**
- Complete flow takes 2-5 seconds
- All components display correctly
- No errors in browser console
- Backend logs show successful retrieval
- 5 documents retrieved
- Sources displayed with relevance scores

---

### **Test 5: Performance & Load** ✅

```bash
# Test multiple concurrent requests
for i in {1..5}; do
  curl -X POST http://localhost:8000/ask \
    -H "Content-Type: application/json" \
    -d '{"question": "Why do customers churn?"}' &
done
wait

# Check resource usage
docker stats --no-stream

# Expected:
# Backend: < 500MB RAM, < 20% CPU
# Qdrant: < 200MB RAM, < 10% CPU
# Frontend: < 100MB RAM, < 5% CPU
```

---

## 🐛 **Troubleshooting**

### **Issue: Backend fails to start**

**Symptoms:**
- Backend container exits immediately
- "RAG system not initialized" errors
- Backend health check fails

**Solutions:**

```bash
# 1. Check backend logs
docker compose logs backend

# Common causes and fixes:

# ❌ OPENAI_API_KEY not set
#    Solution: Add to .env file
echo 'OPENAI_API_KEY=sk-proj-your-key' >> .env
docker compose restart backend

# ❌ Qdrant not ready
#    Solution: Wait for Qdrant health check
docker compose logs qdrant
# Wait for: "Actix runtime found; starting in Actix runtime"

# ❌ Data files missing
#    Solution: Ensure data/*.csv exists
ls -la data/
# Should show: churned_customers_cleaned.csv

# Restart backend
docker compose restart backend
```

---

### **Issue: Frontend shows "Backend Offline"**

**Symptoms:**
- Red dot in top-right corner
- "Backend Offline" message
- Queries fail immediately

**Solutions:**

```bash
# 1. Check if backend is running
docker compose ps backend

# 2. Test backend directly
curl http://localhost:8000/health

# 3. Check backend logs
docker compose logs backend | tail -50

# 4. Check if backend is initializing (wait 30-60s)
docker compose logs backend | grep "RAG system"

# 5. Restart backend if needed
docker compose restart backend

# 6. Check from inside frontend container
docker compose exec frontend curl http://churn-backend:8000/health
```

---

### **Issue: "RAG system not initialized"**

**Symptoms:**
- Backend returns 503 error
- Logs show "RAG system not initialized"
- `/ask` endpoint fails

**Solutions:**

```bash
# Check backend logs for initialization errors
docker compose logs backend | grep -E "Initializing|Error|Failed"

# Common causes:
# 1. API key invalid or expired
# 2. Qdrant connection failed
# 3. Data files not found or corrupted

# Solution 1: Check API key
docker compose exec backend env | grep OPENAI_API_KEY

# Solution 2: Check Qdrant connectivity
docker compose exec backend curl http://qdrant:6333/healthz

# Solution 3: Restart with fresh logs
docker compose down
docker compose up -d
docker compose logs -f backend

# Look for: "✅ RAG system fully initialized and ready!"
```

---

### **Issue: "I couldn't find any relevant information"**

**Symptoms:**
- Backend returns generic "couldn't find information" message
- `documents_found: 0` in metrics
- Sources array is empty

**Solutions:**

```bash
# 1. Check if vector store is populated
curl -s http://localhost:6333/collections/customer_churn | jq '.result.points_count'
# Should return: 68 or more

# 2. Check backend initialization logs
docker compose logs backend | grep "Loaded and indexed"
# Should show: "✓ Loaded and indexed 68 documents"

# 3. Verify parent retriever is initialized
docker compose logs backend | grep "parent retriever"

# 4. If collection is empty, reinitialize
docker compose down -v  # Remove volumes
docker compose up -d    # Recreate everything
```

---

### **Issue: Slow response times (> 10s)**

**Symptoms:**
- Queries take longer than 10 seconds
- Timeouts in frontend
- Poor user experience

**Solutions:**

```bash
# 1. Check resource usage
docker stats

# If memory/CPU high:
# Solution: Increase Docker resources
# Docker Desktop → Settings → Resources → Memory: 4GB+

# 2. Check OpenAI API rate limits
docker compose logs backend | grep -i "rate limit"

# 3. Clear and rebuild
docker compose down -v
docker compose up --build -d

# 4. Reduce concurrent requests
# Check if multiple queries are running simultaneously
```

---

### **Issue: "Collection not found" error**

**Symptoms:**
- Qdrant returns 404 error
- Backend logs show "Collection customer_churn not found"

**Solutions:**

```bash
# Backend needs time to initialize vector store
# Wait 30-60 seconds after backend starts

# Check backend startup progress
docker compose logs backend | tail -20

# Verify collection exists
curl http://localhost:6333/collections | jq

# If collection doesn't exist, backend may have failed to initialize
# Check for errors during startup
docker compose logs backend | grep -i error
```

---

## 🔄 **Common Commands**

### **Viewing Logs**
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f frontend

# Last 100 lines
docker compose logs --tail=100 backend

# Follow new logs only
docker compose logs -f --tail=0 backend

# Search logs for errors
docker compose logs backend | grep -i error
```

### **Restarting Services**
```bash
# Restart all
docker compose restart

# Restart specific service
docker compose restart backend
docker compose restart frontend

# Rebuild and restart (after code changes)
docker compose up --build -d backend

# Full rebuild of all services
docker compose up --build -d
```

### **Stopping Services**
```bash
# Stop all (preserves data)
docker compose stop

# Stop and remove containers (preserves volumes)
docker compose down

# Stop and remove everything including volumes
docker compose down -v

# Use convenience script
./stop-services.sh
```

### **Checking Status**
```bash
# Service status
docker compose ps

# Resource usage
docker stats --no-stream

# Detailed container info
docker compose ps -a

# Network info
docker network inspect churn-network

# Volume info
docker volume ls | grep churn
```

---

## ✅ **Success Criteria Summary**

### **Integration Complete Checklist**

- [ ] `docker compose ps` shows all services "Up" or "Up (healthy)"
- [ ] `curl http://localhost:8000/health` returns 200 with `{"status": "healthy"}`
- [ ] `curl http://localhost:3000` returns HTML (status 200)
- [ ] Frontend shows "Backend Online" with green dot
- [ ] Can submit a question and get response in 2-5 seconds
- [ ] Response includes answer, 5 sources, and metrics
- [ ] Metrics show `"retrieval_method": "parent_document"`
- [ ] `./test-e2e.sh` passes all tests
- [ ] Qdrant dashboard shows 68+ vectors in `customer_churn` collection
- [ ] Backend logs show "✅ RAG system fully initialized and ready!"

### **Performance Benchmarks**

| Metric | Expected | Acceptable | Poor |
|--------|----------|-----------|------|
| Response Time | 2-3s | 3-5s | >5s |
| Documents Retrieved | 5 | 3-5 | <3 |
| Backend Memory | <300MB | <500MB | >500MB |
| Qdrant Memory | <150MB | <200MB | >200MB |
| Frontend Load Time | <2s | <3s | >3s |
| Backend Startup | 30-45s | 45-60s | >60s |

### **Quality Checks**

| Component | Check | Expected |
|-----------|-------|----------|
| **Answer Quality** | Specific and detailed | References actual data from sources |
| **Source Attribution** | 5 sources shown | Each has relevance score >0.7 |
| **Faithfulness** | No hallucinations | All claims traceable to sources |
| **Relevancy** | On-topic answers | Addresses the question directly |
| **Error Handling** | Graceful failures | Clear error messages, no crashes |

---

## 🎯 **Complete Test Script**

The `test-e2e.sh` script validates the entire stack. Run it after startup:

```bash
# Make executable (first time only)
chmod +x test-e2e.sh

# Run tests
./test-e2e.sh

# Expected output:
# 🧪 Starting End-to-End Test
# ==========================
# ⏳ Waiting for services to be ready...
# Testing Qdrant... ✅ PASS
# Testing Backend Health... ✅ PASS
# Testing Frontend... ✅ PASS
# Testing Backend /ask endpoint... ✅ PASS
# ==========================
# ✅ All tests completed!
```

---

## 🎉 **You're Done!**

If all tests pass and checkboxes are checked, your system is **fully integrated and production-ready**!

### **What Changed (Integration Summary)**

**Backend** (`src/backend/api.py`):
- ✅ Real RAG system initialization on startup
- ✅ `/ask` endpoint uses Parent Document retrieval (best performer)
- ✅ `/analyze-churn` endpoint uses LangGraph agent
- ✅ Automatic vector DB creation from data files
- ✅ Performance metrics tracking

**Frontend** (`frontend/src/app/`):
- ✅ TypeScript API client (`api-client.ts`)
- ✅ Calls real backend at http://localhost:8000
- ✅ Backend health monitoring
- ✅ Beautiful response display with sources
- ✅ Error handling with helpful messages

**Docker Compose**:
- ✅ All 4 services defined and working
- ✅ Proper dependencies (Qdrant → Backend → Frontend)
- ✅ Health checks for all services
- ✅ Environment variables configured

---

## 📸 **Expected Results**

### **Qdrant Dashboard**
- Collection: `customer_churn`
- Vector count: 68+
- Status: Green/Healthy

### **Backend API Docs** (http://localhost:8000/docs)
- Shows `/health`, `/ask`, `/analyze-churn` endpoints
- Can test endpoints directly in Swagger UI
- All endpoints return 200 status

### **Frontend Dashboard** (http://localhost:3000)
- Clean, modern UI with blue gradient
- "Backend Online" indicator (green dot)
- Text area with example queries
- "🔍 Analyze with RAG" button
- Responsive and fast

### **Successful Query Response**
- Answer in blue gradient box
- Sources section with 5 documents
- Relevance scores displayed (>0.7)
- Metrics showing <5s response time
- Method: "parent_document"

---

## 📚 **Related Documentation**

- **Integration Details**: `docs/FRONTEND_BACKEND_INTEGRATION.md`
- **RAGAS Evaluation**: `docs/RAGAS_EVALUATION_RESULTS.md`
- **Project Overview**: `docs/PROJECT_OVERVIEW.md`
- **Setup Guide**: `docs/SETUP_GUIDE.md`

---

## 📞 **Still Having Issues?**

### **Quick Diagnostics**

```bash
# Run full diagnostic check
echo "=== Docker ==="
docker --version
docker compose version

echo -e "\n=== Services ==="
docker compose ps

echo -e "\n=== Ports ==="
lsof -i :3000,8000,6333 2>/dev/null || netstat -an | grep -E '3000|8000|6333'

echo -e "\n=== Environment ==="
docker compose exec backend env | grep -E 'OPENAI|QDRANT|COHERE'

echo -e "\n=== Vector Store ==="
curl -s http://localhost:6333/collections/customer_churn | jq '.result.points_count'

echo -e "\n=== Backend Health ==="
curl -s http://localhost:8000/health | jq
```

### **Common Log Locations**

- **Backend initialization**: Look for "🚀 Initializing RAG system"
- **Vector store creation**: Look for "✓ Loaded and indexed"
- **API requests**: Look for "Question received" or "Churn analysis request"
- **Errors**: `docker compose logs backend | grep -i error`

### **Nuclear Option (Fresh Start)**

```bash
# Complete cleanup and restart
docker compose down -v              # Remove everything
docker system prune -f              # Clean Docker cache
rm -rf cache/                       # Clear local cache
docker compose up --build -d        # Rebuild from scratch
docker compose logs -f backend      # Watch initialization
```

### **Resource Requirements**

- **Docker Desktop Memory**: 4GB minimum, 6GB recommended
- **Disk Space**: 5GB free for images and volumes
- **API Keys**: Valid OpenAI API key with sufficient credits

---

**If all else fails**, check:
1. Valid API keys in `.env` file
2. Data files exist in `data/*.csv`
3. Docker Desktop has enough resources
4. No other services using ports 3000, 6333, 8000

---

**Happy testing!** 🚀

