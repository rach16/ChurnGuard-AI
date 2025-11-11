# Quick QA Testing Checklist

**Use this checklist for rapid testing of retrieval methods**

---

## ⚡ 15-Minute Smoke Test

Test these 5 critical questions to verify system health:

- [ ] **Q1**: "What was Dolphin Wave Systems' ARR?" → Should return **$484,785.00**
- [ ] **Q2**: "What are the top 3 churn reasons?" → Should return **Customer Engagement (16), Financial Distress (14), Internal Support (12)**
- [ ] **Q3**: "Compare churn reasons for Dolphin Wave Systems and Bengal Tiger Systems" → Should return **Customer Engagement vs. Financial Distress**
- [ ] **Q4**: "What is ROI for Penguin Slide Tech?" → Should return **"ROI data not available"** (tests hallucination prevention)
- [ ] **Q5**: Response time check → All answers should be **< 5 seconds**

**Pass Criteria**: 5/5 correct answers, no hallucinations, acceptable speed

---

## 🎯 Method Comparison Test (Pick 1 Question, Test All Methods)

**Test Question**: "Why did customers churn in the Commercial segment?"

| Method | Time | Correct? | Quality (1-5) | Notes |
|--------|------|----------|---------------|-------|
| Naive | ___s | ☐ Yes ☐ No | ___/5 | _________________ |
| Multi-Query | ___s | ☐ Yes ☐ No | ___/5 | _________________ |
| Parent Doc | ___s | ☐ Yes ☐ No | ___/5 | _________________ |
| Contextual Comp | ___s | ☐ Yes ☐ No | ___/5 | _________________ |
| Reranking | ___s | ☐ Yes ☐ No | ___/5 | _________________ |

**Winner**: _________________ (Method with highest quality score)

---

## 🔍 Category-Specific Quick Tests

### Factual Accuracy Test (2 min)
- [ ] "What products did Bengal Tiger Systems use?" → **Venison Chunks, Nutritional Consultation**
- [ ] "How long was Wolf Pack Solutions a customer?" → **2.1 years**

### Pattern Analysis Test (3 min)
- [ ] "What patterns exist in Customer Engagement churn cases?" → Should identify **unresponsiveness, low usage**

### Missing Data Test (2 min)
- [ ] "What were customer satisfaction scores?" → Should say **"Data not available"** (not hallucinate)

### Speed Test (1 min)
- [ ] "What is churn?" → Should respond in **< 3 seconds**

---

## 🚨 Critical Failure Checks

Test these to catch serious issues:

- [ ] **No Hallucination**: System doesn't invent facts not in data
- [ ] **Source Attribution**: Every fact has a cited source
- [ ] **Consistency**: Same question → same answer (run twice)
- [ ] **Graceful Errors**: "Data not available" instead of guessing
- [ ] **No Timeout**: All queries complete within 10 seconds

---

## 📊 Daily Health Check

Run this each morning (5 minutes):

**Date**: ___________

| Check | Status | Notes |
|-------|--------|-------|
| System starts without errors | ☐ Pass ☐ Fail | __________ |
| Factual query works | ☐ Pass ☐ Fail | __________ |
| Pattern query works | ☐ Pass ☐ Fail | __________ |
| No hallucinations | ☐ Pass ☐ Fail | __________ |
| Response time < 5s | ☐ Pass ☐ Fail | __________ |

**Overall Status**: ☐ Healthy ☐ Degraded ☐ Down

---

## 🔬 Before/After Testing Template

**Use when testing improvements or changes**

**Change Description**: _______________________

**Test Date**: ___________

| Metric | Before | After | Delta | Better? |
|--------|--------|-------|-------|---------|
| Factual Accuracy | __% | __% | ±__% | ☐ Yes ☐ No |
| Avg Response Time | __s | __s | ±__s | ☐ Yes ☐ No |
| Sources Retrieved | __ | __ | ±__ | ☐ Yes ☐ No |
| User Quality Score | __/5 | __/5 | ±__ | ☐ Yes ☐ No |

**Verdict**: ☐ Deploy ☐ Needs Work ☐ Rollback

---

## 📋 Weekly Deep Dive Checklist

**Week of**: ___________

- [ ] Run all 50+ test questions from main QA doc
- [ ] Compare results to previous week
- [ ] Document new failure modes (list below)
- [ ] Update expected answers if data changed
- [ ] Review user feedback from production
- [ ] Check for performance degradation

**New Issues Found**:
1. ___________________________________
2. ___________________________________
3. ___________________________________

**Action Items**:
- [ ] _________________________________
- [ ] _________________________________
- [ ] _________________________________

---

## 🎯 Pre-Deployment Checklist

**Before pushing to production**:

- [ ] All smoke tests pass (15-min test)
- [ ] No hallucinations detected
- [ ] Performance within SLA (< 5s avg)
- [ ] Error handling works (missing data queries)
- [ ] Multi-method comparison complete
- [ ] Edge cases tested (ambiguous queries)
- [ ] Source citations verified
- [ ] Documentation updated
- [ ] Rollback plan ready

**Sign-off**: _________________ (Name) _________ (Date)

---

## 🐛 Issue Tracking Template

**Issue #**: _____  
**Date Found**: _________  
**Severity**: ☐ Critical ☐ High ☐ Medium ☐ Low

**Query**: "_________________________________"

**Expected**: _______________________________

**Actual**: _______________________________

**Retrieval Method**: _____________________

**Sources Retrieved**: ____________________

**Root Cause**: ☐ Poor retrieval ☐ Wrong sources ☐ LLM error ☐ Data issue ☐ Other: _______

**Action Taken**: _______________________________

**Verified Fixed**: ☐ Yes ☐ No  Date: _________

---

## 🏆 Quality Targets

**Maintain these minimum standards**:

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Factual Accuracy | > 90% | ___% | ☐ Pass ☐ Fail |
| No Hallucinations | 100% | ___% | ☐ Pass ☐ Fail |
| Avg Response Time | < 5s | ___s | ☐ Pass ☐ Fail |
| Source Coverage | > 80% | ___% | ☐ Pass ☐ Fail |
| User Satisfaction | > 4/5 | ___/5 | ☐ Pass ☐ Fail |

**Last Updated**: ___________

---

**Print this page and keep it at your desk for quick testing!**


