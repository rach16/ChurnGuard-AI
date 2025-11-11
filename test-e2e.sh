#!/bin/bash
set -e

echo "🧪 Starting End-to-End Test"
echo "=========================="

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test function
test_endpoint() {
  local name=$1
  local url=$2
  
  echo -n "Testing $name... "
  
  if response=$(curl -s -f "$url" 2>/dev/null); then
    echo -e "${GREEN}✅ PASS${NC}"
    return 0
  else
    echo -e "${RED}❌ FAIL${NC}"
    return 1
  fi
}

# Check if services are running
echo "📋 Checking Docker services..."
if ! docker compose ps | grep -q "Up"; then
  echo -e "${RED}❌ Services not running. Start them with: docker compose up -d${NC}"
  exit 1
fi

# Wait for services
echo "⏳ Waiting for services to be ready..."
sleep 10

# Test Qdrant
test_endpoint "Qdrant Health" "http://localhost:6333/healthz"

# Test Backend
test_endpoint "Backend Health" "http://localhost:8000/health"

# Test Frontend
test_endpoint "Frontend Page" "http://localhost:3000"

# Test Backend /ask endpoint
echo -n "Testing Backend /ask endpoint... "
response=$(curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Why do customers churn?", "max_response_length": 2000}')

if echo "$response" | jq -e '.answer' > /dev/null 2>&1; then
  echo -e "${GREEN}✅ PASS${NC}"
  
  # Show some metrics
  response_time=$(echo "$response" | jq -r '.metrics.response_time_ms')
  docs_found=$(echo "$response" | jq -r '.metrics.documents_found')
  echo "   📊 Response time: ${response_time}ms, Documents: ${docs_found}"
else
  echo -e "${RED}❌ FAIL${NC}"
  echo "   Response: $response"
fi

# Check Qdrant collection
echo -n "Testing Qdrant Collection... "
collection_info=$(curl -s http://localhost:6333/collections/customer_churn)
if echo "$collection_info" | jq -e '.result' > /dev/null 2>&1; then
  vector_count=$(echo "$collection_info" | jq -r '.result.vectors_count // 0')
  echo -e "${GREEN}✅ PASS${NC}"
  echo "   📦 Vectors indexed: ${vector_count}"
else
  echo -e "${YELLOW}⚠️  WARNING${NC}"
  echo "   Collection may not be initialized yet. Wait 30s and retry."
fi

echo ""
echo "=========================="
echo "✅ All core tests completed!"
echo ""
echo "📊 Services available at:"
echo "   🎨 Frontend:  http://localhost:3000"
echo "   🔧 Backend:   http://localhost:8000/docs"
echo "   💾 Qdrant:    http://localhost:6333/dashboard"
echo "   📓 Jupyter:   http://localhost:8888"
echo ""
echo "🧪 Manual test: Open http://localhost:3000 and ask a question!"

