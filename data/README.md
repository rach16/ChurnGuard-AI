# Data Folder

Place your customer churn data files here.

## 🎉 Flexible Data Format

**Your data works as-is!** Whether you have:
- ✅ Salesforce pulse/health history
- ✅ Custom CRM exports
- ✅ Customer engagement metrics
- ✅ Support ticket data
- ✅ Any CSV with customer information

The system adapts to your data structure. See `SALESFORCE_DATA_GUIDE.md` for SFDC-specific instructions.

## Expected Data Structure

### CSV Files (Customer Data)
```
customer_churn_data.csv
├── customer_id          (string) - Unique customer identifier
├── tenure_months        (int) - Customer tenure in months
├── monthly_charges      (float) - Monthly service charges
├── total_charges        (float) - Total charges to date
├── churn_label          (int) - 1 if churned, 0 if retained
├── contract_type        (string) - Month-to-month, One year, Two year
├── payment_method       (string) - Electronic check, Credit card, etc.
├── internet_service     (string) - DSL, Fiber optic, No
├── support_tickets      (int) - Number of support tickets
└── ... (additional features)
```

### PDF Documents
- **Retention Policies**: Business policies and procedures for customer retention
- **Churn Analysis Reports**: Historical analysis of churn patterns
- **Industry Research**: External research and benchmarking reports

### Text Files
- **Customer Feedback**: Unstructured customer feedback and comments
- **Support Transcripts**: Customer support conversation logs
- **Survey Responses**: Free-text survey responses

## Sample Data

If you don't have data yet, you can:

1. **Generate synthetic data** using the provided notebook:
   ```python
   # In Jupyter notebook
   from src.utils.data_loader import generate_sample_churn_data
   df = generate_sample_churn_data(n_customers=1000)
   df.to_csv('data/customer_churn_data.csv', index=False)
   ```

2. **Use public datasets**:
   - [Kaggle Telco Customer Churn](https://www.kaggle.com/datasets/blastchar/telco-customer-churn)
   - [UCI ML Churn Dataset](https://archive.ics.uci.edu/ml/datasets/Churn+Modelling)

## Data Privacy

⚠️ **Important**: Ensure all customer data is:
- Anonymized (no PII)
- Compliant with data privacy regulations (GDPR, CCPA, etc.)
- Authorized for use in development/analysis

## File Formats Supported

| Format | Use Case | Loader |
|--------|----------|--------|
| `.csv` | Structured customer data | `pandas.read_csv()` |
| `.pdf` | Reports and policies | `PyMuPDF` or `pypdf` |
| `.txt` | Unstructured feedback | `TextLoader` |
| `.json` | API responses | `json.load()` |

## Processing Pipeline

1. **Load** → Load raw data from files
2. **Clean** → Handle missing values, normalize
3. **Transform** → Create text representations for RAG
4. **Embed** → Generate vector embeddings
5. **Store** → Save to Qdrant vector database

See `src/utils/data_loader.py` for implementation details.

---
