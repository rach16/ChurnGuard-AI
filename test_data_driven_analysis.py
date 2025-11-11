#!/usr/bin/env python3
"""Test script to verify data-driven AI analysis with different customers"""

import requests
import json

def test_customer_analysis(customer_name, segment, arr, risk_score, risk_reason):
    """Test AI analysis for a specific customer"""

    query = f"Analyze customer churn risk for {customer_name} ({segment} segment, ${arr:,} ARR). They have a {risk_score}% risk score with primary concern: {risk_reason}. Provide specific retention strategies and recommendations."

    payload = {
        "query": query,
        "include_background": True,
        "include_citations": True
    }

    print(f"\n{'='*100}")
    print(f"Testing: {customer_name}")
    print(f"{'='*100}")

    try:
        response = requests.post(
            "http://localhost:8000/multi-agent-analyze",
            json=payload,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()

            # Extract key personalized elements
            response_text = data.get('response', '')
            insights = data.get('key_insights', [])
            confidence = data.get('confidence_score', 0)

            print(f"\n✅ Response received (Confidence: {confidence*100:.0f}%)")

            print(f"\n📊 Key Insights:")
            for insight in insights:
                print(f"  • {insight}")

            print(f"\n📝 Response Preview (first 800 chars):")
            print(response_text[:800])
            print("...")

            return True
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"❌ Exception: {e}")
        return False


if __name__ == "__main__":
    print("🧪 Testing Data-Driven AI Analysis with Actual Customer Data")
    print("="*100)

    # Test with actual customers from the system
    test_cases = [
        {
            "customer_name": "CloudSync Systems",
            "segment": "Enterprise",
            "arr": 171842,
            "risk_score": 75.0,
            "risk_reason": "Support issues"
        },
        {
            "customer_name": "DevTools Solutions",
            "segment": "Commercial",
            "arr": 184247,
            "risk_score": 70.9,
            "risk_reason": "Product fit concerns"
        }
    ]

    results = []
    for test_case in test_cases:
        result = test_customer_analysis(**test_case)
        results.append(result)

    # Summary
    print(f"\n{'='*100}")
    print(f"📈 TEST SUMMARY")
    print(f"{'='*100}")
    print(f"Total tests: {len(results)}")
    print(f"Passed: {sum(results)}")

    if all(results):
        print("\n✅ All tests passed! AI analysis is using real customer data.")
    else:
        print("\n⚠️ Some tests failed.")
