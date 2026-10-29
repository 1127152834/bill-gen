/* ============================================================
 * 示例数据（SAMPLE 版）
 * 结构与版式联动关系与原稿一致；机构名、个人姓名、账号、地址、
 * 交易对手与金额均已替换为虚构示例值。
 * PDF 元数据默认值与原稿一致（可在编辑器中修改）。
 * ============================================================ */
window.SAMPLE_DATA = {
  meta: {
    title: "Statement",                                   // 原稿 Title
    author: "Registered to: BUSDATA ",                    // 原稿 Author
    subject: "",                                          // 原稿 Subject
    keywords: "",                                         // 原稿 Keywords
    creator: "OpenText Exstream Version 24.1.0 64-bit",   // 原稿 Creator
    producer: "",                                         // 原稿 Producer
    creationDate: "",                                       // 空=按 Statement Period 止动态生成
    pdfFileName: "",                                       // 导出 PDF 文件名（留空 = 账号-statement.pdf）
    tray: "Tray2",                                         // 原稿隐藏托盘标记（白色不可见）
    sheet: "1"
  },
  watermark: { enabled: true, text: "SAMPLE", color: "#0F6191", opacity: 0.10 },
  branding: {
    institution: "Sample Credit Union",
    phone: "800-555-0198  ",
    website: "samplecreditunion.example",
    inquiriesLabel: "Direct Inquiries to:",
    addrLine1: "PO Box 66945, 11545 W. Touhy Avenue",  // 固化(与原稿一致)
    addrLine2: "Springfield, IL 62701-1234",
    logo: ""
  },
  statement: {
    docTitle: "Statement of Account ",  // 固化(与原稿一致)
    accountNumber: "12345678",
    periodStart: "06/01/26",
    periodEnd: "06/30/26"
  },
  customer: {
    lines: ["ALEX SAMPLE", "APT 5B", "1500 MAIN ST", "ANYTOWN OR 97201"]
  },
  accounts: [
    {
      id: "01",
      name: "SAVINGS ACCOUNT",
      beginning: 215.40,
      apy: "",
      dividendsEarned: "",
      ytdDividends: 3.14,
      note: "",
      transactions: [
        { date: "06/01/26", desc: "DEPOSIT ONLINE BANKING TRANSFER FROM SHARE 40  ", amount: 340.00, dir: "deposit" },
        { date: "06/04/26", desc: "WITHDRAWAL ONLINE BANKING TRANSFER TO SHARE 40  ", amount: 510.00, dir: "withdrawal" }
      ]
    },
    {
      id: "40",
      name: "CHECKING",
      beginning: 128.36,
      apy: "0.20%",
      dividendsEarned: 0.02,
      ytdDividends: 0.02,
      note: "ANNUAL PERCENTAGE YIELD EARNED 0.20% FOR PERIOD FROM 06/01/26 THRU 06/30/26 BASED ON AVERAGE DAILY BALANCE OF : $95.75.",
      transactions: [
        { date: "06/01/26", desc: "DEPOSIT ACH TYPE: TRANSFER ID: FIRST SAMPLE BANK N.A. DFI ACCT: XX90123 ACH WEB XFER ", amount: 415.00, dir: "deposit" },
        { date: "06/01/26", desc: "WITHDRAWAL ONLINE BANKING TRANSFER TO SHARE 01  ", amount: 340.00, dir: "withdrawal" },
        { date: "06/04/26", desc: "DEPOSIT ONLINE BANKING TRANSFER FROM SHARE 01  ", amount: 510.00, dir: "deposit" },
        { date: "06/05/26", desc: "WITHDRAWAL ACH SAMPLE BROKERAGE LLC TYPE: MONEYLINE ID: 0123456789 CO: SAMPLE BROKERAGE LLC NAME: ALEX SAMPLE ", amount: 510.00, dir: "withdrawal" },
        { date: "06/30/26", desc: "DEPOSIT DIVIDEND ", amount: 0.02, dir: "deposit" }
      ]
    }
  ],
  disclosure: {
    heading: "In Case of Errors or Questions About Your Electronic Transfers",  // 固化(与原稿一致)
    paragraphs: [
          "Contact us as soon as you can if you think your statementor receiptis wrong or if you needmore informationabouta transfer listed on the statementreceipt.We must hear from you no later than 60 days after we sent the FIRST statementon which the problemor error appeared.",
          "To file a dispute contact us by:",
          "• Calling toll-free:800-328-1935or +1-773-462-2000",
          "• Writing via U.S. Mail to Alliant Credit Union, PO Box 66945, Chicago,IL 60666-0945.Include: (1) Name and account number (2) Descriptionof the error or the transfer you are unsure about,and explainas clearlyas you can why you believeit is an error or why you needmore information,(3) Dollaramountof the suspected error.",
          "• Loggingin to Alliant onlinebankingand filing a dispute (for debit and savings ATM card(s) only)",
          "We will investigateyour complaintand will correct any error promptly.If we take more than 10 business days to do this, we will credit your account for the amount you think is in error, so that you will havethe use of the moneyduring the time it takes us to completeour investigation."
    ]  // 固化(与原稿一致)
  },
  reporting: {
    year: "2026",
    label: "Total DividendsPaid Yearto Date:",
    valueOverride: "",
    promoUrl: ""                 // 空 = 原稿第 2 页位图 LOGO（assets/promo-logo.jpg）
  }
};
