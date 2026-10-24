import { useMemo, useRef, useState } from 'react';
import { getProducts, getDecisionRules, ALL_CATEGORIES, ALL_SUPPLIERS } from '../services/dataService';
import useData from '../hooks/useData';
import KPICard from '../components/KPICard';
import DataTable from '../components/DataTable';
import { Loading } from '../components/Pending';
import { HBars } from '../components/charts';
import FsnBandModal from '../components/FsnBandModal';
import Icon from '../components/Icon';
import InfoTip from '../components/InfoTip';
import { num, FSN_TONE, FSN_LABEL, RULE, RULE_ORDER } from '../lib/format';

/**
 * Classification (FSN).
 *
 * Written for whoever runs the store, not for whoever built the model. The
 * numbers are unchanged — what changed is that the page no longer asks the
 * reader to know what ADUS, CV%, a censored day or an 80th-percentile cutoff
 * is. Those live in step3_fsn_classification.py and in the docs; a person
 * deciding what to reorder does not need them on screen.
 *
 * Deliberately removed:
 *   - Threshold Sensitivity Analysis (how the F/S/N split moves at the 75th,
 *     80th and 85th percentile cutoffs). That is a methods-section exhibit,
 *     not an operating one.
 *   - The ADUS, CV% and Stockout columns, for the same reason.
 *
 * The decision rules (manuscript Sec. 3.3.1) come from /api/fsn/decision-rules
 * (backend/decision_rules.py): each item's action, and for "Review for
 * Discontinuation" the two semesters it was judged on. Nothing about the rule
 * is decided here; the page only words it and lets the threshold be changed.
 */
/* Fast, Slow, Non-Moving — the reading order of the page, used for the item
   list's default sort. Not alphabetical, which would give F, N, S. */
const FSN_ORDER = { F: 0, S: 1, N: 2 };

/* Units per selling day. The backend's default is marked in the list; any
   other value is sent as ?threshold=. */
const THRESHOLD_CHOICES = [0.05, 0.1, 0.2, 0.3, 0.5];

/** "AY2526-T1: 0.007 a day (1 unit)" for each semester a review was judged on. */
function semesterTrail(readings) {
  return (readings ?? []).map(r => r.on_sheet
    ? `${r.semester_id}: ${r.adus.toFixed(3)} a day (${num(r.units)} unit${r.units === 1 ? '' : 's'})`
    : `${r.semester_id}: not on the tally sheet`).join(' · ');
}

export default function Classification({ filters }) {
  const { data: products, loading } = useData(() => getProducts(filters), [filters], [],
    { key: `classification:products:${filters.supplier}|${filters.category}|${filters.dateRange}` });
  // null = the backend's default threshold, which is also the request the
  // prefetch warms.
  const [threshold, setThreshold] = useState(null);
  const { data: rules } = useData(() => getDecisionRules(threshold ?? undefined), [threshold], null,
    { key: `classification:rules:${threshold ?? 'default'}` });

  const [band, setBand] = useState(null);       // 'F' | 'S' | 'N' | null
  const [bestBand, setBestBand] = useState('All');
  const [q, setQ] = useState('');
  const [fsn, setFsn] = useState('All');
  const [action, setAction] = useState('All');
  const [category, setCategory] = useState(ALL_CATEGORIES);
  const [supplier, setSupplier] = useState(ALL_SUPPLIERS);
  const listRef = useRef(null);

  // Each product with its rule. Joined here rather than served on
  // /api/products because the threshold changes the rule without changing
  // the catalogue, and the catalogue request is the expensive one.
  const ruleById = useMemo(
    () => new Map((rules?.items ?? []).map(r => [r.product_id, r])), [rules]);
  const ruled = useMemo(() => products.map(p => {
    const r = ruleById.get(p.product_id);
    return {
      ...p,
      action: r?.action ?? null,
      action_rank: r?.action ? RULE_ORDER.indexOf(r.action) : 99,
      semesters: r?.semesters ?? [],
    };
  }), [products, ruleById]);
  const ruleCounts = useMemo(() => {
    const c = Object.fromEntries(RULE_ORDER.map(k => [k, 0]));
    ruled.forEach(p => { if (p.action in c) c[p.action]++; });
    return c;
  }, [ruled]);

  /** "Show these items": the rule becomes the list's only filter, and the
      list scrolls into view, since it is below the fold. */
  function showRule(key) {
    setAction(key);
    setFsn('All');
    setQ('');
    setCategory(ALL_CATEGORIES);
    setSupplier(ALL_SUPPLIERS);
    listRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  const fsnCounts = useMemo(() => {
    const c = { F: 0, S: 0, N: 0 };
    products.forEach(p => { if (c[p.fsn_class] !== undefined) c[p.fsn_class]++; });
    return c;
  }, [products]);
  const hvlCount = useMemo(() => products.filter(p => p.is_hvl).length, [products]);
  const withSales = useMemo(() => products.filter(p => p.total_units > 0).length, [products]);
  const total = products.length || 1;

  // Ranked on units rather than revenue because only part of the catalogue
  // carries a unit price - a revenue ranking would sort on a half-empty column.
  // `bestBand` is this card's own filter, deliberately separate from the item
  // list's: the two answer different questions and should not move together.
  const showSupplier = filters.supplier === ALL_SUPPLIERS;
  const topSellers = useMemo(() => [...products]
    .filter(p => p.total_units > 0 && (bestBand === 'All' || p.fsn_class === bestBand))
    .sort((a, b) => b.total_units - a.total_units)
    .slice(0, 10)
    .map(p => ({
      name: p.item_name,
      value: p.total_units,
      // See Overview: the supplier line is for the all-suppliers view only.
      sub: showSupplier ? p.supplier_name : undefined,
    })), [products, bestBand, showSupplier]);

  const categories = useMemo(
    () => [...new Set(products.map(p => p.category))].sort(), [products]);
  const suppliers = useMemo(
    () => [...new Set(products.map(p => p.supplier_name))].sort(), [products]);

  const needle = q.trim().toLowerCase();
  // Unsorted, the list opens in catalogue order, which puts no question the
  // reader has at the top. The default is the order the page argues for: the
  // groups in the order the three cards above name them, and inside each
  // group the items that move the most. Any column heading still overrides it.
  const shown = useMemo(() => ruled.filter(p =>
    (!needle
      || p.item_name.toLowerCase().includes(needle)
      || p.supplier_name.toLowerCase().includes(needle)
      || p.category.toLowerCase().includes(needle))
    && (fsn === 'All' || p.fsn_class === fsn)
    && (action === 'All' || p.action === action)
    && (category === ALL_CATEGORIES || p.category === category)
    && (supplier === ALL_SUPPLIERS || p.supplier_name === supplier)
  ).sort((a, b) =>
    (FSN_ORDER[a.fsn_class] ?? 9) - (FSN_ORDER[b.fsn_class] ?? 9)
    || (b.avg_monthly ?? 0) - (a.avg_monthly ?? 0)
  ), [ruled, needle, fsn, action, category, supplier]);

  // Widths sum to 100 (see Overview's ProductsModal for why that matters).
  const columns = [
    { key: 'item_name',     label: 'Product Name', strong: true, truncate: true, width: '25%' },
    { key: 'category',      label: 'Category', truncate: true, width: '12%' },
    { key: 'supplier_name', label: 'Supplier', truncate: true, width: '17%' },
    { key: 'total_units',   label: 'Units Sold', num: true, strong: true, width: '9%', render: num },
    {
      key: 'avg_monthly', label: 'Avg / Month', num: true, width: '9%',
      render: v => (v ?? 0).toFixed(1),
    },
    {
      key: 'fsn_class', label: 'Classification', width: '14%',
      render: (v, row) => (
        <span style={{ display: 'inline-flex', gap: 5 }}>
          <span className={`tag tag--${FSN_TONE[v]}`}>{FSN_LABEL[v]}</span>
          {row.is_hvl === 1 && <span className="tag tag--hvl" title="Sells quickly, but on few recorded days">Thin history</span>}
        </span>
      ),
    },
    {
      // Sorted on the rule's display order, not its name.
      key: 'action_rank', label: 'Action', width: '14%',
      render: (_v, row) => {
        const rule = RULE[row.action];
        if (!rule) return <span className="nodata">&mdash;</span>;
        const why = row.action === 'review_discontinuation' ? semesterTrail(row.semesters) : undefined;
        return <span className={`tag tag--${rule.tone}`} title={why}>{rule.label}</span>;
      },
    },
  ];

  const filtered = shown.length !== products.length;

  if (loading) return <Loading label="Loading classification…" />;

  return (
    <div className="stack">
      {/* Total first, then the three bands it splits into. The three counts
          only mean something against the size of the catalogue they came
          from - 47 Fast is a different screen at 136 products than at 600. */}
      <div className="grid-4">
        <KPICard
          label="Total Products"
          value={num(products.length)}
          sub={`${num(withSales)} with recorded sales · ${num(products.length - withSales)} without`}
          icon="box"
        />
        <KPICard label="Fast-Moving" value={fsnCounts.F} tone="ok" icon="zap" accent
          sub={`${Math.round(fsnCounts.F / total * 100)}% of products · sell regularly`}
          onClick={() => setBand('F')} linkLabel="See the Fast-Moving items" />
        <KPICard label="Slow-Moving" value={fsnCounts.S} icon="clock"
          sub={`${Math.round(fsnCounts.S / total * 100)}% of products · sell occasionally`}
          onClick={() => setBand('S')} linkLabel="See the Slow-Moving items" />
        <KPICard label="Non-Moving" value={fsnCounts.N} tone="crit" icon="xCircle"
          sub={`${Math.round(fsnCounts.N / total * 100)}% of products · no recorded sales`}
          onClick={() => setBand('N')} linkLabel="See the Non-Moving items" />
      </div>

      {/* Best sellers, full width. The decision rules that sat beside them
          are reference rather than a report, so they open from the item
          list's "Decision rules (i)" button instead (see DecisionRules). */}
      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">Best Sellers</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="hint">
              top 10 by units sold{bestBand !== 'All' ? ` · ${FSN_LABEL[bestBand]} only` : ''}
            </span>
            <select className="inline-select" value={bestBand}
                    onChange={e => setBestBand(e.target.value)}
                    aria-label="Filter best sellers by group">
              <option value="All">All Groups</option>
              <option value="F">{FSN_LABEL.F}</option>
              <option value="S">{FSN_LABEL.S}</option>
              <option value="N">{FSN_LABEL.N}</option>
            </select>
          </span>
        </div>
        {topSellers.length === 0
          ? <div className="empty">No {FSN_LABEL[bestBand]?.toLowerCase() ?? ''} product has recorded sales.</div>
          : <HBars data={topSellers} color="var(--ink)" valueFmt={num} />}
      </div>

      <div className="card card__pad" ref={listRef} style={{ scrollMarginTop: 12 }}>
        <div className="card-h">
          <span className="section-h">FSN Classification Item List</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            {/* The manuscript's decision rule card (Sec. 3.3.1), as a help
                panel: the key to the Action column below. */}
            <DecisionRules
              rules={rules}
              counts={ruleCounts}
              hvlCount={hvlCount}
              onShow={showRule}
              threshold={threshold}
              setThreshold={setThreshold}
            />
            {/* A count, as a figure rather than a sentence - it is the one
                number on this header and it changes with every filter, so it
                should be readable at a glance from across the card. */}
            <span className="count-pill">
              <b>{num(shown.length)}</b>
              {filtered ? <> of {num(products.length)} products</> : <> products</>}
            </span>
          </span>
        </div>

        {/* Every control says what it filters. The tray had four unlabelled
            boxes reading "All Groups / All Categories / All Suppliers", which
            is three ways of saying "All" and no way of saying of what. */}
        <div className="filter-row">
          <div className="filter-field filter-field--grow">
            <label className="filter-field__label" htmlFor="cl-search">Search</label>
            <input
              id="cl-search"
              type="search"
              value={q}
              placeholder="Item, supplier or category…"
              onChange={e => setQ(e.target.value)}
            />
          </div>
          <div className="filter-field">
            <label className="filter-field__label" htmlFor="cl-group">Classification</label>
            <select id="cl-group" value={fsn} onChange={e => setFsn(e.target.value)}>
              <option value="All">All Groups</option>
              <option value="F">{FSN_LABEL.F}</option>
              <option value="S">{FSN_LABEL.S}</option>
              <option value="N">{FSN_LABEL.N}</option>
            </select>
          </div>
          <div className="filter-field">
            <label className="filter-field__label" htmlFor="cl-action">Action</label>
            <select id="cl-action" value={action} onChange={e => setAction(e.target.value)}>
              <option value="All">All Actions</option>
              {RULE_ORDER.map(k => <option key={k} value={k}>{RULE[k].label}</option>)}
            </select>
          </div>
          <div className="filter-field">
            <label className="filter-field__label" htmlFor="cl-category">Category</label>
            <select id="cl-category" value={category} onChange={e => setCategory(e.target.value)}>
              <option value={ALL_CATEGORIES}>{ALL_CATEGORIES}</option>
              {categories.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="filter-field">
            <label className="filter-field__label" htmlFor="cl-supplier">Supplier</label>
            <select id="cl-supplier" value={supplier} onChange={e => setSupplier(e.target.value)}>
              <option value={ALL_SUPPLIERS}>{ALL_SUPPLIERS}</option>
              {suppliers.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>


        {shown.length === 0
          ? <div className="empty">No product matches those filters.</div>
          : <DataTable columns={columns} data={shown} pageSize={10} minWidth={880} />}
      </div>

      {/* No onOpenFullList: the full list is the card directly behind this
          modal, so the button would point at itself. */}
      <FsnBandModal band={band} onClose={() => setBand(null)} products={products} />
    </div>
  );
}

/* ------------------------------------------------------------ decision rules */

/** What to do with each group (manuscript Sec. 3.3.1), with how many of the
 *  products on screen it applies to and a link that lists them.
 *
 *  It is reference, not a report: the Action column already says what to do
 *  with each item, so the rules open from a "Decision rules (i)" button on the
 *  item list instead of taking a card on the page. A "Show N items" link
 *  filters the list, which scrolls it into view and so closes the panel.
 *
 *  `counts` are over the products the topbar filters leave, so they agree with
 *  the item list. The Review row carries the threshold control because that is
 *  the one rule with a number in it the store may want to move. */
function DecisionRules({ rules, counts, hvlCount, onShow, threshold, setThreshold }) {
  const semesters = rules?.semesters ?? [];
  const judgedOn = semesters.length
    ? semesters.map(s => s.semester_id).join(' and ')
    : 'the last two semesters';
  const dflt = rules?.default_threshold;
  const selected = threshold ?? dflt;
  const choices = dflt != null && !THRESHOLD_CHOICES.includes(dflt)
    ? [...THRESHOLD_CHOICES, dflt].sort((a, b) => a - b)
    : THRESHOLD_CHOICES;

  const rows = [
    {
      key: 'keep_stocked',
      name: 'Fast-moving',
      text: <>Sells regularly. Forecast every 30 days and reorder at the reorder point &mdash; running
        out costs the most sales.</>,
    },
    {
      key: 'promote',
      name: 'Slow-moving',
      text: <>Sells now and then. Stock smaller quantities, and move it with bundled pricing, better
        shelf placement, or targeted marketing during enrollment and campus events.</>,
    },
    {
      key: 'review_discontinuation',
      name: 'Slow for two semesters',
      text: <>Sold under {rules ? rules.threshold : '…'} units per selling day in both {judgedOn}.
        Decide whether to keep carrying it.</>,
      control: (
        <label className="hint" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          Threshold
          <select className="inline-select" value={selected ?? ''} disabled={selected == null}
                  onChange={e => {
                    const v = Number(e.target.value);
                    setThreshold(v === dflt ? null : v);
                  }}
                  aria-label="Review for discontinuation threshold, units per selling day">
            {choices.map(v => (
              <option key={v} value={v}>{v} / day{v === dflt ? ' (default)' : ''}</option>
            ))}
          </select>
        </label>
      ),
    },
    {
      key: 'clear_out',
      name: 'Non-moving',
      text: <>No recorded sales, so it is never reordered. Return it to the supplier, mark it down for
        clearance, or discontinue it.</>,
    },
  ];
  if (counts.discontinued > 0) {
    rows.push({
      key: 'discontinued',
      name: 'Discontinued',
      text: <>Marked discontinued by the store; no rule applies.</>,
    });
  }

  return (
    <InfoTip text="Decision rules" label="Decision rules" width={460}>
      <div className="infotip__h">What to do with each group</div>
      <div className="explain">
        {rows.map(r => (
          <div className="explain__row" key={r.key}>
            <span className={`tag tag--${RULE[r.key].tone}`}>{RULE[r.key].label}</span>
            <div>
              <p><b>{r.name}.</b> {r.text}</p>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginTop: 4 }}>
                {rules && (counts[r.key] > 0
                  ? (
                    <button type="button" className="linkish" onClick={() => onShow(r.key)}>
                      Show {num(counts[r.key])} item{counts[r.key] === 1 ? '' : 's'} <Icon name="arrow" size={12} />
                    </button>
                  )
                  : <span className="hint">No items</span>)}
                {r.control}
              </div>
            </div>
          </div>
        ))}
        {hvlCount > 0 && (
          <div className="explain__row">
            <span className="tag tag--hvl">Thin history</span>
            <p>
              {hvlCount} fast-selling item{hvlCount === 1 ? '' : 's'}{hvlCount === 1 ? ' has' : ' have'} only
              been counted on a handful of days. {hvlCount === 1 ? 'It sells' : 'They sell'} quickly, but there is
              less history behind that, so treat the figure as less certain.
            </p>
          </div>
        )}
      </div>
    </InfoTip>
  );
}
