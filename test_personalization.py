#!/usr/bin/env python3
"""Test script to verify customer-specific AI analysis personalization"""

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

    print(f"\n{'='*80}")
    print(f"Testing: {customer_name}")
    print(f"{'='*80}")
    print(f"Query: {query[:100]}...")

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

            print(f"\n✅ Response received!")
            print(f"\n📊 Key Insights:")
            for insight in insights:
                print(f"  • {insight}")

            print(f"\n📝 Response Preview (first 500 chars):")
            print(response_text[:500])

            # Verify personalization
            print(f"\n🔍 Personalization Check:")
            checks = {
                f"Customer name '{customer_name}'": customer_name in response_text,
                f"Segment '{segment}'": segment in response_text,
                f"ARR '${arr:,}'": f"${arr:,}" in response_text or str(arr) in response_text,
                f"Risk reason '{risk_reason}'": risk_reason in response_text.lower()
            }

            for check, passed in checks.items():
                status = "✓" if passed else "✗"
                print(f"  {status} {check}: {'FOUND' if passed else 'MISSING'}")

            return all(checks.values())
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"❌ Exception: {e}")
        return False


if __name__ == "__main__":
    print("🧪 Testing Customer-Specific AI Analysis Personalization")
    print("="*80)

    # Test different customer scenarios
    test_cases = [
        {
            "customer_name": "Acme Corporation",
            "segment": "Enterprise",
            "arr": 150000,
            "risk_score": 85.5,
            "risk_reason": "pricing concerns"
        },
        {
            "customer_name": "TechStart Solutions",
            "segment": "SMB",
            "arr": 25000,
            "risk_score": 72.3,
            "risk_reason": "low engagement"
        },
        {
            "customer_name": "Global Enterprises Inc",
            "segment": "Commercial",
            "arr": 65000,
            "risk_score": 68.9,
            "risk_reason": "competitive pressure"
        }
    ]

    results = []
    for test_case in test_cases:
        result = test_customer_analysis(**test_case)
        results.append(result)

    # Summary
    print(f"\n{'='*80}")
    print(f"📈 TEST SUMMARY")
    print(f"{'='*80}")
    print(f"Total tests: {len(results)}")
    print(f"Passed: {sum(results)}")
    print(f"Failed: {len(results) - sum(results)}")

    if all(results):
        print("\n✅ All tests passed! Customer-specific personalization is working correctly.")
    else:
        print("\n⚠️ Some tests failed. Personalization may need adjustment.")
