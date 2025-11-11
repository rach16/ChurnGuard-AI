# Retrieval Method QA Test Questions

**Purpose**: Systematically test and compare retrieval methods to identify strengths, weaknesses, and optimal use cases.

**How to Use**: 
1. Run each question through all 5 retrieval methods (Naive, Multi-Query, Parent Document, Contextual Compression, Reranking)
2. Compare answer quality, sources retrieved, response time, and accuracy
3. Identify which method performs best for each question type
4. Document failure cases and edge cases

---

## 📋 Test Categories

- [Factual Queries (Exact Recall)](#1-factual-queries-exact-recall)
- [Analytical Queries (Pattern Recognition)](#2-analytical-queries-pattern-recognition)
- [Comparison Queries (Multi-Entity)](#3-comparison-queries-multi-entity)
- [Temporal Queries (Time-Based)](#4-temporal-queries-time-based)
- [Aggregation Queries (Statistical)](#5-aggregation-queries-statistical)
- [Competitive Intelligence (External Knowledge)](#6-competitive-intelligence-external-knowledge)
- [Edge Cases & Failure Modes](#7-edge-cases--failure-modes)
- [Performance & Scalability](#8-performance--scalability-tests)

---

## 1. Factual Queries (Exact Recall)

**What This Tests**: Ability to retrieve precise facts, numbers, dates, and specific customer details.

**Expected Best Method**: Parent Document (needs full context for accuracy)

### Basic Factual Retrieval

**Q1.1**: What was Dolphin Wave Systems' Annual Recurring Revenue (ARR) before they churned?
- **Expected Answer**: $484,785.00
- **Tests**: Exact number retrieval
- **Pass Criteria**: Correct dollar amount (not approximation)
- **Failure Mode**: Returning wrong customer's ARR or "approximately $485K"

**Q1.2**: How many years was Wolf Pack Solutions a customer before churning?
- **Expected Answer**: 2.1 years
- **Tests**: Specific tenure data
- **Pass Criteria**: Exact tenure figure
- **Failure Mode**: Rounded number (2 years) or wrong customer

**Q1.3**: What specific products did Bengal Tiger Systems use?
- **Expected Answer**: Venison Chunks, Nutritional Consultation
- **Tests**: Complete product list retrieval
- **Pass Criteria**: All products listed, no hallucinations
- **Failure Mode**: Partial list or products from another customer

**Q1.4**: What was the churn date for Penguin Slide Tech?
- **Expected Answer**: 8/23/2024
- **Tests**: Exact date retrieval
- **Pass Criteria**: Correct date in correct format
- **Failure Mode**: Wrong date, approximation ("late August"), or wrong customer

**Q1.5**: Which customer segment does Magpie Collect Data belong to?
- **Expected Answer**: Enterprise
- **Tests**: Metadata field retrieval
- **Pass Criteria**: Correct segment classification
- **Failure Mode**: Wrong segment or generic answer

### Multi-Attribute Factual Queries

**Q1.6**: For Armadillo Shell Systems, what was their ARR, tenure, and primary churn reason?
- **Expected Answer**: ARR: $640,000.00, Tenure: 1.0 years, Reason: Not ICP match
- **Tests**: Multiple fact retrieval from same entity
- **Pass Criteria**: All three facts correct
- **Failure Mode**: Missing any fact, mixing with other customers

**Q1.7**: List all products used by Butterfly Flutter Analytics along with their churn reason.
- **Expected Answer**: Products: PetHub Apple Slices, Species Tracker, Hypoallergenic Diet, Travel Certificate Service; Reason: Internal Support/Champion - No Response
- **Tests**: Complex multi-field retrieval
- **Pass Criteria**: Complete product list + correct churn reason
- **Failure Mode**: Incomplete product list, wrong reason

---

## 2. Analytical Queries (Pattern Recognition)

**What This Tests**: Ability to identify trends, patterns, and synthesize insights across multiple documents.

**Expected Best Method**: Multi-Query (needs diverse perspectives and comprehensive coverage)

### Pattern Identification

**Q2.1**: What are the top 3 churn reasons across all customer segments?
- **Expected Answer**: Customer Engagement (16), Financial Distress (14), Internal Support/Champion (12)
- **Tests**: Aggregation across all customers
- **Pass Criteria**: Correct top 3 with counts
- **Failure Mode**: Wrong order, missing counts, or incorrect reasons

**Q2.2**: What common patterns exist among customers who churned due to "Customer Engagement"?
- **Expected Answer**: Unresponsive to outreach, declined renewal discussions, low product usage indicators
- **Tests**: Pattern synthesis from multiple cases
- **Pass Criteria**: Identifies 3+ common themes with examples
- **Failure Mode**: Generic answer without specific patterns

**Q2.3**: How does churn behavior differ between Commercial and Enterprise segments?
- **Expected Answer**: Commercial: More "Internal Support/Champion" issues (7 customers), shorter tenure (2.0 years); Enterprise: More financial distress, longer tenure
- **Tests**: Comparative pattern analysis
- **Pass Criteria**: Identifies segment-specific differences with data
- **Failure Mode**: Vague generalizations without segment comparison

**Q2.4**: What product combinations are most commonly associated with churn?
- **Expected Answer**: Analysis of product bundles in churned accounts
- **Tests**: Cross-product pattern recognition
- **Pass Criteria**: Identifies 2-3 common product combinations with frequency
- **Failure Mode**: Lists random products without pattern analysis

### Trend Analysis

**Q2.5**: Are there any seasonal trends in churn throughout 2024?
- **Expected Answer**: Analysis by quarter/month showing concentrations
- **Tests**: Temporal pattern recognition
- **Pass Criteria**: Identifies time-based clusters (if any)
- **Failure Mode**: "No patterns found" without actually analyzing dates

**Q2.6**: What warning signs appear before customers churn due to financial distress?
- **Expected Answer**: Company acquisitions, budget cuts, RIF mentions, territory cost concerns
- **Tests**: Early indicator identification
- **Pass Criteria**: Lists 3-4 early warning signals with examples
- **Failure Mode**: Only states "financial problems" without specific indicators

---

## 3. Comparison Queries (Multi-Entity)

**What This Tests**: Ability to retrieve and compare information across multiple customers or segments.

**Expected Best Method**: Multi-Query or Parent Document (needs comprehensive context)

**Q3.1**: Compare the churn reasons for Dolphin Wave Systems, Wolf Pack Solutions, and Bengal Tiger Systems.
- **Expected Answer**: 
  - Dolphin: Customer Engagement
  - Wolf Pack: Financial Distress
  - Bengal Tiger: Financial Distress
- **Tests**: Multi-entity fact retrieval
- **Pass Criteria**: Correct reason for each customer
- **Failure Mode**: Mixing up customers, partial answers

**Q3.2**: Which customers had the highest ARR losses, and what were their churn reasons?
- **Expected Answer**: Top customers ranked by ARR with their specific churn reasons
- **Tests**: Sorting + multi-attribute retrieval
- **Pass Criteria**: Top 5 in correct order with reasons
- **Failure Mode**: Wrong order, missing ARR values, incorrect reasons

**Q3.3**: Compare average tenure between customers who churned due to "Financial Distress" vs. "Customer Engagement."
- **Expected Answer**: Calculate and compare average tenure for each reason
- **Tests**: Aggregation by category + comparison
- **Pass Criteria**: Correct averages for both categories with comparison
- **Failure Mode**: No calculation, approximate answers, wrong grouping

**Q3.4**: How do the products used by Commercial customers differ from those used by Enterprise customers?
- **Expected Answer**: Product usage breakdown by segment
- **Tests**: Segment-based product analysis
- **Pass Criteria**: Identifies distinct product preferences per segment
- **Failure Mode**: Generic product list without segment differentiation

**Q3.5**: Which competitors appear most frequently as alternatives, and which customer segments are they targeting?
- **Expected Answer**: Competitor frequency count + segment breakdown
- **Tests**: Multi-entity pattern + segmentation
- **Pass Criteria**: Ranked competitor list with segment associations
- **Failure Mode**: Incomplete competitor list, no segment analysis

---

## 4. Temporal Queries (Time-Based)

**What This Tests**: Ability to handle date-based filtering, chronological ordering, and time-range queries.

**Expected Best Method**: Parent Document or Reranking (needs precise date context)

**Q4.1**: Which customers churned in Q3 2024 (July-September)?
- **Expected Answer**: List of customers with close dates in July-Sept 2024
- **Tests**: Date range filtering
- **Pass Criteria**: All customers in range, none outside range
- **Failure Mode**: Missing customers, including wrong months

**Q4.2**: What was the average time between a customer's first win date and their churn date?
- **Expected Answer**: Calculate tenure from First Win Date to Close Date
- **Tests**: Date arithmetic across multiple records
- **Pass Criteria**: Correct average tenure calculation
- **Failure Mode**: Wrong calculation, missing tenure data

**Q4.3**: Show the churn timeline for October 2024 - which customers churned each week?
- **Expected Answer**: Week-by-week breakdown of October 2024 churns
- **Tests**: Temporal grouping and ordering
- **Pass Criteria**: Chronological ordering with correct week assignments
- **Failure Mode**: Wrong dates, incorrect week groupings

**Q4.4**: Which customer had the shortest tenure before churning, and what was their churn reason?
- **Expected Answer**: Customer with minimum tenure + reason
- **Tests**: MIN aggregation + fact retrieval
- **Pass Criteria**: Correct customer with shortest tenure + reason
- **Failure Mode**: Wrong customer, incorrect tenure calculation

**Q4.5**: How many customers churned within their first year vs. after 2+ years?
- **Expected Answer**: Count breakdown: <1 year, 1-2 years, >2 years
- **Tests**: Tenure-based bucketing and counting
- **Pass Criteria**: Correct counts for each bucket
- **Failure Mode**: Wrong counts, missing categories

---

## 5. Aggregation Queries (Statistical)

**What This Tests**: Ability to perform calculations, aggregations, and statistical analysis.

**Expected Best Method**: Structured query (SQL) if available, otherwise Multi-Query (needs all relevant docs)

**Q5.1**: What is the total ARR lost across all churned customers?
- **Expected Answer**: $6,187,907 (sum of all ARR losses)
- **Tests**: SUM aggregation
- **Pass Criteria**: Exact total (or within $100 due to rounding)
- **Failure Mode**: Wrong total, approximation, missing customers

**Q5.2**: What percentage of churn is attributed to "Financial Distress" vs. other reasons?
- **Expected Answer**: 14 out of 68 customers = 20.6%
- **Tests**: COUNT + percentage calculation
- **Pass Criteria**: Correct percentage (±1%)
- **Failure Mode**: Wrong count, incorrect percentage formula

**Q5.3**: What is the average ARR for customers in the Commercial segment vs. Enterprise segment?
- **Expected Answer**: Calculate avg ARR per segment
- **Tests**: GROUP BY segment + AVG calculation
- **Pass Criteria**: Correct averages for both segments
- **Failure Mode**: Wrong grouping, incorrect average

**Q5.4**: Which customer segment has the highest total ARR loss?
- **Expected Answer**: Commercial: $2,200,222 (or correct segment based on data)
- **Tests**: GROUP BY segment + SUM + MAX
- **Pass Criteria**: Correct segment with correct total
- **Failure Mode**: Wrong segment, incorrect sum

**Q5.5**: What is the median tenure of churned customers?
- **Expected Answer**: Calculate median from all tenure values
- **Tests**: MEDIAN calculation (not mean)
- **Pass Criteria**: Correct median value
- **Failure Mode**: Returns mean instead of median, wrong calculation

---

## 6. Competitive Intelligence (External Knowledge)

**What This Tests**: Ability to combine internal data with external search (Tavily integration).

**Expected Best Method**: Multi-Agent System (Research Team with Tavily)

**Q6.1**: Which competitors are mentioned most frequently in churn records, and what are they known for?
- **Expected Answer**: List of competitors from churn data + external research on their offerings
- **Tests**: Internal data + external knowledge synthesis
- **Pass Criteria**: Competitor list from data + accurate external info
- **Failure Mode**: Only internal data or hallucinated external info

**Q6.2**: What are the key differentiators of Dolphin Wave Tech that may have attracted our customers?
- **Expected Answer**: External research on Dolphin Wave Tech's features/pricing
- **Tests**: External search capability
- **Pass Criteria**: Real information about competitor (if exists) or clear "no information found"
- **Failure Mode**: Hallucinated features without external validation

**Q6.3**: How do our churn rates compare to industry benchmarks for B2B SaaS companies?
- **Expected Answer**: Internal churn rate + external benchmark data
- **Tests**: Calculation + external research
- **Pass Criteria**: Correct internal rate + cited external benchmark
- **Failure Mode**: No external data or uncited claims

**Q6.4**: What retention strategies have proven effective in similar industries?
- **Expected Answer**: External research on industry best practices
- **Tests**: Pure external knowledge retrieval
- **Pass Criteria**: Cited external sources with specific strategies
- **Failure Mode**: Generic advice without external sources

---

## 7. Edge Cases & Failure Modes

**What This Tests**: System robustness, error handling, and behavior with challenging queries.

**Expected Behavior**: Graceful degradation, clear "no data" responses, no hallucinations.

### Ambiguous Queries

**Q7.1**: "Tell me about the tigers."
- **Challenge**: Ambiguous reference (Bengal Tiger Systems? Tiger product?)
- **Pass Criteria**: Asks for clarification or returns both possibilities
- **Failure Mode**: Randomly picks one without acknowledging ambiguity

**Q7.2**: "Why did they leave?"
- **Challenge**: No entity specified ("they")
- **Pass Criteria**: Asks "which customer?" or provides general churn summary
- **Failure Mode**: Returns random customer data without clarification

**Q7.3**: "What about the prices?"
- **Challenge**: Vague query without context
- **Pass Criteria**: Asks for clarification (ARR? Product prices? Competitor pricing?)
- **Failure Mode**: Hallucinates price data

### Missing Data Queries

**Q7.4**: "What was the ROI for Penguin Slide Tech?"
- **Challenge**: ROI data doesn't exist in dataset
- **Pass Criteria**: Clearly states "ROI data not available" and explains what data exists
- **Failure Mode**: Calculates fake ROI or hallucinates data

**Q7.5**: "Which customers gave us 5-star reviews?"
- **Challenge**: Review data doesn't exist
- **Pass Criteria**: "No review data in system" + suggests alternative queries
- **Failure Mode**: Invents review data

**Q7.6**: "What was the weather when Customer X churned?"
- **Challenge**: Completely irrelevant/nonsensical query
- **Pass Criteria**: Politely explains data scope limitations
- **Failure Mode**: Attempts to answer or hallucinates weather data

### Data Conflict Queries

**Q7.7**: "How many customers are in the Commercial segment?" (then verify against actual count)
- **Challenge**: Tests consistency of counting
- **Pass Criteria**: Correct count (33 customers based on provided data)
- **Failure Mode**: Different counts on repeated queries

**Q7.8**: "What was Eagle Eye Technologies' churn reason?" (ask twice with different retrieval methods)
- **Challenge**: Tests consistency across methods
- **Pass Criteria**: Same answer from all methods
- **Failure Mode**: Different answers depending on method

### Complex Multi-Hop Queries

**Q7.9**: "Which competitor won the most customers who previously used our AnimalCare Portal product?"
- **Challenge**: Requires joining customers → products → competitors
- **Pass Criteria**: Correct multi-hop reasoning with source citations
- **Failure Mode**: Missing one of the joins, incorrect association

**Q7.10**: "For customers who churned to competitors, what was the average time between their first complaint and churn date?"
- **Challenge**: Data likely doesn't exist (complaint dates)
- **Pass Criteria**: Clearly states missing data + suggests proxy metrics
- **Failure Mode**: Invents complaint dates

### Negation Queries

**Q7.11**: "Which customers did NOT churn due to financial reasons?"
- **Challenge**: Negation/exclusion logic
- **Pass Criteria**: Lists customers excluding financial distress category
- **Failure Mode**: Returns customers WITH financial distress

**Q7.12**: "Show me Enterprise customers who never used the AnimalCare Portal."
- **Challenge**: Absence detection (NOT IN logic)
- **Pass Criteria**: Correct exclusion filtering
- **Failure Mode**: Returns customers who DID use AnimalCare Portal

---

## 8. Performance & Scalability Tests

**What This Tests**: Response time, resource usage, and behavior under load.

**Q8.1**: Simple query baseline: "What is churn?"
- **Metric**: Response time (should be <3 seconds)
- **Tests**: Baseline performance

**Q8.2**: Complex query: "Analyze all churn patterns across all segments with competitor analysis and recommendations."
- **Metric**: Response time (acceptable up to 10 seconds)
- **Tests**: Complex query handling

**Q8.3**: Repeated identical query (3x): "What was Dolphin Wave Systems' ARR?"
- **Metric**: Should benefit from caching (if implemented)
- **Tests**: Cache effectiveness

**Q8.4**: Batch query: Ask 10 factual questions in sequence
- **Metric**: Total time vs. individual query time × 10
- **Tests**: Batch processing efficiency

**Q8.5**: Concurrent queries: Submit 5 queries simultaneously
- **Metric**: Response time vs. sequential queries
- **Tests**: Concurrent request handling

---

## 🎯 QA Testing Scorecard Template

Use this scorecard for each question:

| Question ID | Method | Correct? | Sources Retrieved | Response Time | Quality Score (1-5) | Notes |
|-------------|--------|----------|-------------------|---------------|---------------------|-------|
| Q1.1 | Naive | Yes/No | # docs | X.X sec | X/5 | Missing product detail |
| Q1.1 | Multi-Query | Yes/No | # docs | X.X sec | X/5 | Comprehensive but slow |
| Q1.1 | Parent Doc | Yes/No | # docs | X.X sec | X/5 | Perfect answer |
| Q1.1 | Contextual Comp | Yes/No | # docs | X.X sec | X/5 | Too aggressive filtering |
| Q1.1 | Reranking | Yes/No | # docs | X.X sec | X/5 | Good relevance |

---

## 🔬 Recommended Testing Workflow

### Phase 1: Baseline Testing (30 minutes)
1. Run **all factual queries (Q1.1-Q1.7)** with **Naive method** only
2. Document expected vs. actual answers
3. Establish baseline accuracy and response time

### Phase 2: Method Comparison (2 hours)
1. Select 10 diverse questions (2 from each category)
2. Run each question through **all 5 retrieval methods**
3. Compare quality, speed, and source relevance
4. Identify method-specific strengths

### Phase 3: Edge Case Validation (1 hour)
1. Run all **Edge Case queries (Q7.1-Q7.12)**
2. Focus on error handling and robustness
3. Document failure modes and hallucinations

### Phase 4: Performance Testing (30 minutes)
1. Run **Performance tests (Q8.1-Q8.5)**
2. Measure response times under load
3. Test caching effectiveness (if implemented)

### Phase 5: Competitive Analysis (1 hour)
1. Run **Competitive Intelligence queries (Q6.1-Q6.4)**
2. Validate external search integration (Tavily)
3. Check citation accuracy

---

## 📊 Expected Results Summary

| Query Type | Best Method | Worst Method | Key Insight |
|------------|-------------|--------------|-------------|
| **Factual** | Parent Document | Contextual Compression | Need full context for accuracy |
| **Analytical** | Multi-Query | Naive | Diverse perspectives matter |
| **Comparison** | Multi-Query / Parent | Contextual Compression | Need comprehensive coverage |
| **Temporal** | Parent Document | Contextual Compression | Date context easily filtered out |
| **Aggregation** | Multi-Query | Contextual Compression | Need all relevant documents |
| **Competitive** | Multi-Agent (Tavily) | All RAG-only | External search essential |
| **Edge Cases** | Parent Document | Contextual Compression | More context = better safety |

---

## 🐛 Common Issues to Watch For

### Hallucination Detection
- **Test**: Compare answer against source documents
- **Red Flag**: Specific facts not in retrieved sources
- **Example**: System says "ARR was $500K" but no source contains that number

### Source Attribution Failures
- **Test**: Verify every claim has a source citation
- **Red Flag**: Confident answers without source references
- **Example**: "The customer left due to pricing" with no cited document

### Context Loss
- **Test**: Ask follow-up questions requiring previous context
- **Red Flag**: System loses thread of conversation
- **Example**: Q: "Why did Dolphin churn?" → A: [answer] → Q: "What was their ARR?" → A: "Which customer?"

### Aggregation Errors
- **Test**: Manually verify counts and sums
- **Red Flag**: Numbers don't match source data
- **Example**: System says "14 customers" but manual count shows 16

### Method Inconsistency
- **Test**: Same question, different methods, compare answers
- **Red Flag**: Contradictory answers from different methods
- **Example**: Naive says "2 products" but Parent Doc says "4 products" for same customer

---

## 🎓 What Good Retrieval Looks Like

### ✅ High-Quality Answer Characteristics:
1. **Accurate**: Facts match source documents exactly
2. **Complete**: Doesn't omit critical information
3. **Sourced**: Every claim traceable to a document
4. **Relevant**: Directly answers the question asked
5. **Concise**: No unnecessary information
6. **Honest**: States "data not available" when appropriate

### ❌ Poor Answer Characteristics:
1. **Hallucinated**: Contains facts not in sources
2. **Incomplete**: Missing key information present in sources
3. **Unsourced**: Makes claims without citations
4. **Off-topic**: Answers a different question
5. **Verbose**: Includes irrelevant information
6. **Overconfident**: Guesses when data is missing

---

## 📝 Continuous Testing Recommendations

### Daily Smoke Tests
Run these 5 questions daily to catch regressions:
1. Q1.1 (factual recall)
2. Q2.1 (pattern analysis)
3. Q3.1 (multi-entity)
4. Q7.4 (missing data handling)
5. Q8.1 (performance baseline)

### Weekly Deep Dive
- Run full suite (50+ questions)
- Compare week-over-week metrics
- Review new failure modes
- Update expected answers if data changes

### Production Monitoring
- Log all user queries
- Flag queries with low confidence scores
- Review queries that timeout
- Collect user feedback (thumbs up/down)

---

**Last Updated**: October 21, 2025  
**Test Coverage**: 50+ questions across 8 categories  
**Estimated Testing Time**: 5-6 hours for complete suite


