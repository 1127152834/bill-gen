// Synthetic defaults; no original customer record is included.
window.SAMPLE_DATA = {
  "meta": {
    "title": "Fictional Sample Statement",
    "author": "Bill Gen",
    "subject": "Fictional sample data",
    "keywords": "sample, fictional",
    "creator": "Bill Gen",
    "producer": "Bill Gen",
    "creationDate": "D:20261001000000Z",
    "modDate": "D:20261001000000Z",
    "pdfFileName": "fictional-sample-statement.pdf",
    "tray": "",
    "sheet": ""
  },
  "watermark": {
    "enabled": true,
    "text": "SAMPLE",
    "color": "#0F6191",
    "opacity": 0.1
  },
  "branding": {
    "institution": "Alliant Credit Union",
    "phone": "000-000-0000",
    "website": "example.invalid",
    "inquiriesLabel": "Fictional example",
    "addrLine1": "100 EXAMPLE STREET",
    "addrLine2": "DEMO CITY CA 90000",
    "logo": ""
  },
  "statement": {
    "docTitle": "Sample Statement",
    "accountNumber": "000000000001",
    "periodStart": "10/01/26",
    "periodEnd": "10/31/26"
  },
  "customer": {
    "lines": [
      "ALEX EXAMPLE",
      "100 EXAMPLE STREET",
      "DEMO CITY CA 90000",
      ""
    ]
  },
  "accounts": [
    {
      "id": "01",
      "name": "Savings",
      "beginning": 1000,
      "apy": "0.00%",
      "dividendsEarned": 0,
      "ytdDividends": 0,
      "note": "Fictional sample account.",
      "transactions": [
        {
          "date": "10/02/2026",
          "desc": "Sample deposit",
          "amount": 250,
          "dir": "deposit"
        },
        {
          "date": "10/07/2026",
          "desc": "Sample purchase",
          "amount": 35.5,
          "dir": "withdrawal"
        }
      ]
    },
    {
      "id": "02",
      "name": "Checking",
      "beginning": 1000,
      "apy": "0.00%",
      "dividendsEarned": 0,
      "ytdDividends": 0,
      "note": "Fictional sample account.",
      "transactions": [
        {
          "date": "10/02/2026",
          "desc": "Sample deposit",
          "amount": 250,
          "dir": "deposit"
        },
        {
          "date": "10/07/2026",
          "desc": "Sample purchase",
          "amount": 35.5,
          "dir": "withdrawal"
        }
      ]
    }
  ],
  "disclosure": {
    "heading": "About this fictional sample",
    "paragraphs": [
      "This paragraph contains fictional example content. No real customer information is included.",
      "This paragraph contains fictional example content. No real customer information is included.",
      "This paragraph contains fictional example content. No real customer information is included.",
      "This paragraph contains fictional example content. No real customer information is included.",
      "This paragraph contains fictional example content. No real customer information is included.",
      "This paragraph contains fictional example content. No real customer information is included."
    ]
  },
  "reporting": {
    "year": "2026",
    "label": "Example dividends",
    "valueOverride": "",
    "promoUrl": ""
  }
};
