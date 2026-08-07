/* USTore data layer — ported from the prototype's data.js, exposed on window.USTORE */
(function () {
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const LEAD_TIME = 14;
  const Z_SCORE = 1.65;
  const ACTIVE_SELLING_DAYS = 365;
  const HVL_IDS = new Set([6, 16]);
  const N_THRESHOLD = 0.3;

  const PRODUCTS = [
    { id: 1,  name: 'UST Varsity Jacket',        category: 'Apparel',         supplier: 'Thomasian Merch Co.',  price: 1850, monthlySales: [95,80,85,90,100,160,130,175,110,105,170,200], currentStock: 280 },
    { id: 2,  name: 'UST College Hoodie',         category: 'Apparel',         supplier: 'Thomasian Merch Co.',  price: 1200, monthlySales: [110,95,100,105,115,180,155,200,130,120,190,220], currentStock: 190 },
    { id: 3,  name: 'UST Basic Polo Shirt',       category: 'Apparel',         supplier: 'Thomasian Merch Co.',  price: 650,  monthlySales: [130,115,120,125,140,210,180,240,155,145,220,260], currentStock: 310 },
    { id: 4,  name: 'UST PE Shorts',              category: 'Apparel',         supplier: 'Thomasian Merch Co.',  price: 450,  monthlySales: [35,28,30,32,40,55,45,60,38,35,42,50], currentStock: 85 },
    { id: 5,  name: 'UST Sports Socks (3-pack)',  category: 'Apparel',         supplier: 'Thomasian Merch Co.',  price: 220,  monthlySales: [5,3,4,2,6,8,4,7,3,2,5,4], currentStock: 90 },
    { id: 6,  name: 'Tiger Logo Cap',             category: 'Accessories',     supplier: 'Golden Tiger Supplies', price: 450, monthlySales: [80,72,75,78,88,140,115,155,95,90,135,160], currentStock: 60 },
    { id: 7,  name: 'UST Tote Bag',               category: 'Accessories',     supplier: 'Golden Tiger Supplies', price: 380, monthlySales: [22,18,20,21,25,35,30,40,24,22,30,36], currentStock: 55 },
    { id: 8,  name: 'UST Lanyard',                category: 'Accessories',     supplier: 'Campus Essentials PH',  price: 120, monthlySales: [150,130,140,145,160,240,200,270,170,160,235,280], currentStock: 380 },
    { id: 9,  name: 'UST Wristband',              category: 'Accessories',     supplier: 'Campus Essentials PH',  price: 75,  monthlySales: [30,25,27,28,33,48,40,55,35,30,44,52], currentStock: 120 },
    { id: 10, name: 'UST Face Mask (5-pack)',      category: 'Accessories',     supplier: 'Campus Essentials PH',  price: 95,  monthlySales: [8,5,6,4,7,10,6,9,4,3,6,5], currentStock: 200 },
    { id: 11, name: 'UST Ballpen Set',             category: 'School Supplies', supplier: 'Campus Essentials PH',  price: 85,  monthlySales: [200,170,185,190,210,310,265,350,220,210,300,360], currentStock: 520 },
    { id: 12, name: 'UST Notebook (80 leaves)',    category: 'School Supplies', supplier: 'Campus Essentials PH',  price: 145, monthlySales: [180,155,165,170,190,280,240,315,200,190,270,330], currentStock: 400 },
    { id: 13, name: 'UST Folder Set',              category: 'School Supplies', supplier: 'Campus Essentials PH',  price: 65,  monthlySales: [40,33,36,37,42,60,52,68,44,40,55,66], currentStock: 95 },
    { id: 14, name: 'UST Sticky Notes Set',        category: 'School Supplies', supplier: 'Campus Essentials PH',  price: 55,  monthlySales: [28,23,25,26,30,42,36,48,31,28,38,46], currentStock: 140 },
    { id: 15, name: 'UST Scientific Calculator',   category: 'School Supplies', supplier: 'Golden Tiger Supplies', price: 580, monthlySales: [6,4,5,3,7,12,8,14,5,4,8,6], currentStock: 45 },
    { id: 16, name: 'UST Tumbler 600ml',           category: 'Drinkware',       supplier: 'Golden Tiger Supplies', price: 550, monthlySales: [75,65,70,72,82,125,105,140,88,83,120,145], currentStock: 50 },
    { id: 17, name: 'UST Mug 350ml',               category: 'Drinkware',       supplier: 'Golden Tiger Supplies', price: 320, monthlySales: [25,20,22,23,27,38,32,44,28,25,36,42], currentStock: 70 },
    { id: 18, name: 'UST Insulated Bottle 1L',     category: 'Drinkware',       supplier: 'Golden Tiger Supplies', price: 750, monthlySales: [18,14,16,17,20,28,24,32,20,18,26,30], currentStock: 38 },
    { id: 19, name: 'UST Glass Tumbler',           category: 'Drinkware',       supplier: 'Thomasian Merch Co.',  price: 420,  monthlySales: [5,3,4,2,5,8,5,9,4,3,6,4], currentStock: 55 },
    { id: 20, name: 'UST Keychain',                category: 'Souvenirs',       supplier: 'Campus Essentials PH',  price: 95,  monthlySales: [100,88,93,96,108,162,138,180,115,108,158,190], currentStock: 145 },
    { id: 21, name: 'UST Ref Magnet',              category: 'Souvenirs',       supplier: 'Campus Essentials PH',  price: 85,  monthlySales: [32,26,28,29,34,50,42,56,36,32,46,55], currentStock: 88 },
    { id: 22, name: 'UST Enamel Pin Set',          category: 'Souvenirs',       supplier: 'Golden Tiger Supplies', price: 195, monthlySales: [20,16,18,19,22,32,27,36,23,20,29,35], currentStock: 65 },
    { id: 23, name: 'UST Photo Frame',             category: 'Souvenirs',       supplier: 'Thomasian Merch Co.',  price: 350,  monthlySales: [4,2,3,2,4,7,4,8,3,2,5,4], currentStock: 72 },
    { id: 24, name: 'UST Miniature Trophy',        category: 'Souvenirs',       supplier: 'Thomasian Merch Co.',  price: 480,  monthlySales: [3,2,2,1,3,5,3,6,2,1,4,3], currentStock: 40 },
    { id: 25, name: 'UST Pennant Flag',            category: 'Souvenirs',       supplier: 'Golden Tiger Supplies', price: 220, monthlySales: [15,12,13,14,17,24,20,28,18,15,22,26], currentStock: 48 },
    { id: 26, name: 'UST Class Ring',              category: 'Accessories',     supplier: 'Thomasian Merch Co.',  price: 2800, monthlySales: [2,1,1,0,2,4,2,5,1,1,3,2], currentStock: 18 },
    { id: 27, name: 'UST Umbrella',                category: 'Accessories',     supplier: 'Golden Tiger Supplies', price: 680, monthlySales: [10,8,12,18,25,30,20,22,24,18,14,12], currentStock: 32 },
    { id: 28, name: 'UST Polo Barong',             category: 'Apparel',         supplier: 'Thomasian Merch Co.',  price: 1100, monthlySales: [20,15,18,19,22,30,26,35,21,20,28,34], currentStock: 42 },
  ];

  function stdDev(arr) {
    const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
    return Math.sqrt(arr.reduce((s, x) => s + (x - mean) ** 2, 0) / arr.length);
  }

  function classifyFSN(products) {
    const withADUS = products.map(p => ({ ...p, adus: Math.round((p.monthlySales.reduce((a, b) => a + b, 0) / ACTIVE_SELLING_DAYS) * 1000) / 1000 }));
    const sortedAsc = [...withADUS].sort((a, b) => a.adus - b.adus);
    const n = sortedAsc.length;
    const p80 = sortedAsc[Math.min(Math.floor(n * 0.80), n - 1)].adus;
    return withADUS.map(p => {
      const isHVL = HVL_IDS.has(p.id);
      const fsn_class = (isHVL || p.adus > p80) ? 'F' : (p.adus <= N_THRESHOLD ? 'N' : 'S');
      return { ...p, fsn_class, is_hvl: isHVL };
    });
  }

  const PRODUCTS_ENRICHED = classifyFSN(PRODUCTS).map(p => {
    const totalUnits = p.monthlySales.reduce((a, b) => a + b, 0);
    const avgMonthly = totalUnits / 12;
    const avgDaily = avgMonthly / 30;
    const adus = totalUnits / ACTIVE_SELLING_DAYS;
    const sigma = stdDev(p.monthlySales);
    const cv = avgMonthly > 0 ? (sigma / avgMonthly) * 100 : 0;
    const safetyStock = Math.ceil(Z_SCORE * sigma * Math.sqrt(LEAD_TIME / 30));
    const rop = Math.ceil(avgDaily * LEAD_TIME + safetyStock);
    const eoq = Math.ceil(Math.sqrt((2 * totalUnits * 50) / (p.price * 0.25)));
    const totalRevenue = totalUnits * p.price;
    const status = p.currentStock < rop ? 'REORDER NOW' : p.currentStock < rop * 1.15 ? 'APPROACHING' : 'OK';
    const daysOfStock = avgDaily > 0 ? Math.round(p.currentStock / avgDaily) : 999;
    const prescriptive_advisory = status === 'REORDER NOW'
      ? (p.is_hvl ? `Strategic Re-run Advisory: Coordinate with ${p.supplier} for limited restock. Lead time: 14 days.` : `Order ${eoq} units from ${p.supplier}. Lead time: 14 days.`)
      : status === 'APPROACHING'
      ? `Monitor — projected to reach ROP within ${Math.max(1, Math.round((p.currentStock - rop) / Math.max(avgDaily, 0.01)))} days.`
      : null;
    return { ...p, totalUnits, avgMonthly: Math.round(avgMonthly * 10) / 10, adus: Math.round(adus * 1000) / 1000,
      cv: Math.round(cv * 10) / 10, safetyStock, rop, eoq, totalRevenue, status, daysOfStock, prescriptive_advisory };
  });

  const MONTHLY_REVENUE = MONTHS.map((month, i) => ({ month, revenue: PRODUCTS_ENRICHED.reduce((s, p) => s + p.monthlySales[i] * p.price, 0) }));

  function getParetoData() {
    const sorted = [...PRODUCTS_ENRICHED].sort((a, b) => b.totalRevenue - a.totalRevenue);
    const total = sorted.reduce((s, p) => s + p.totalRevenue, 0);
    let cum = 0;
    return sorted.map(p => { cum += p.totalRevenue; return { name: p.name.length > 18 ? p.name.slice(0, 16) + '…' : p.name, revenue: p.totalRevenue, cumPct: Math.round((cum / total) * 1000) / 10 }; });
  }

  const ADVISORIES = [
    { event: 'Semestral Break (Ongoing)', timeframe: 'May 2026 — demand slowdown in effect now',
      impact: 'Overall store demand is approximately 65% below regular-semester levels.',
      recommendations: ['Defer non-critical purchase orders until June enrollment begins', 'Conduct physical inventory audit and reconcile tally records', 'Use this window to pre-coordinate with suppliers for enrollment stock'],
      severity: 'low' },
    { event: '1st Semester Enrollment', timeframe: 'Begins approximately 3–4 weeks from now (June 2026)',
      impact: 'School Supplies and Apparel demand historically increases by 45–55% during enrollment.',
      recommendations: ['Pre-position stock for UST Ballpen Set (current: 520 — forecast: 310 units in June)', 'Pre-position stock for UST Notebook 80 leaves (current: 400 — forecast: 280 units)', 'Monitor UST Basic Polo Shirt (current: 310 — approaching enrollment-period ROP)'],
      severity: 'high' },
    { event: 'Foundation Day & Paskuhan (Nov–Dec)', timeframe: 'Approximately 6 months away',
      impact: 'Souvenirs, Apparel, and Drinkware demand spikes 35–40% in November–December.',
      recommendations: ['Begin early supplier coordination for Tiger Logo Cap (HVL — Strategic Re-run Advisory)', 'Build buffer stock for UST Lanyard and UST Keychain ahead of Paskuhan demand', 'Review Drinkware supplier lead times for UST Tumbler 600ml (currently REORDER NOW)'],
      severity: 'moderate' },
  ];

  const RECENT_ENTRIES = [
    { date: '2026-05-05', item: 'UST Ballpen Set',          qty: 12, supplier: 'Campus Essentials PH', type: 'Sale' },
    { date: '2026-05-05', item: 'UST Notebook (80 leaves)', qty: 8,  supplier: 'Campus Essentials PH', type: 'Sale' },
    { date: '2026-05-04', item: 'UST Lanyard',              qty: 3,  supplier: 'Campus Essentials PH', type: 'Return' },
    { date: '2026-05-04', item: 'Tiger Logo Cap',           qty: 5,  supplier: 'Golden Tiger Supplies', type: 'Sale' },
    { date: '2026-05-03', item: 'UST Varsity Jacket',       qty: 2,  supplier: 'Thomasian Merch Co.',  type: 'Sale' },
    { date: '2026-05-03', item: 'UST Tumbler 600ml',        qty: 4,  supplier: 'Golden Tiger Supplies', type: 'Sale' },
    { date: '2026-05-02', item: 'UST Keychain',             qty: 10, supplier: 'Campus Essentials PH', type: 'Sale' },
    { date: '2026-05-01', item: 'UST Mug 350ml',            qty: 1,  supplier: 'Golden Tiger Supplies', type: 'Damage' },
  ];

  const SUPPLIERS = ['All Suppliers', 'Thomasian Merch Co.', 'Golden Tiger Supplies', 'Campus Essentials PH'];
  const CATEGORIES = ['All Categories', 'Apparel', 'Accessories', 'School Supplies', 'Drinkware', 'Souvenirs'];
  const TRANSACTION_TYPES = ['Sale', 'Return', 'Damage', 'Internal Transfer'];

  window.USTORE = {
    MONTHS, PRODUCTS, PRODUCTS_ENRICHED, MONTHLY_REVENUE, getParetoData,
    ADVISORIES, RECENT_ENTRIES, SUPPLIERS, CATEGORIES, TRANSACTION_TYPES,
  };
})();
