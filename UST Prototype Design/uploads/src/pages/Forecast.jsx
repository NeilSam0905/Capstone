import { useState, useMemo } from 'react';
import { getProducts, getForecast, getForecastMetrics, getProductHistory } from '../services/dataService';
import useData from '../hooks/useData';
import Pending, { Loading } from '../components/Pending';
import { LineChart } from '../components/charts';
import { num, shortMonth, FSN_TONE, FSN_LABEL } from '../lib/format';

/**
 * Demand Forecast.
 *
 * The forecast itself is NOT rendered: step4_prophet_forecast.py has not
 * been re-run, so Result_Forecast does not exist. The previous version of
 * this screen synthesised a Prophet line with Math.sin() and hardcoded a
 * MAPE per FSN class, which contradicted the project's own benchmark
 * finding. What stays is the screen's structure, the SKU selector, and
 * the item's REAL monthly history.
 */
export default function Forecast({ filters }) {
  const { data: products, loading } = useData(() => getProducts(filters), [filters], []);
  const { data: forecast } = useData(getForecast, []);
  const { data: metrics } = useData(getForecastMetrics, []);
  const [selectedId, setSelectedId] = useState(null);

  const withHistory = useMemo(
    () => products.filter(p => p.total_units > 0).sort((a, b) => b.total_units - a.total_units),
    [products]
  );
  const product = withHistory.find(p => p.product_id === selectedId) ?? withHistory[0] ?? null;

  if (loading) return <Loading label="Loading products…" />;
  if (!product) return <div className="empty">No products match this filter.</div>;

  return (
    <div className="stack">
      <div className="card card__pad">
        <div className="card-h" style={{ marginBottom: 0 }}>
          <div className="filter" style={{ gap: 10 }}>
            <select
              value={product.product_id}
              onChange={e => setSelectedId(Number(e.target.value))}
              style={{ minWidth: 320 }}
            >
              {withHistory.map(p => (
                <option key={p.product_id} value={p.product_id}>
                  {p.item_name} ({num(p.total_units)} units)
                </option>
              ))}
            </select>
            <span className="filter__chev">▾</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className={`tag tag--${FSN_TONE[product.fsn_class]}`}>{FSN_LABEL[product.fsn_class]}</span>
            {product.is_hvl === 1 && <span className="tag tag--hvl">HVL</span>}
            <span className="hint">
              ADUS {product.adus.toFixed(3)} · CV {product.cv.toFixed(0)}% · {product.active_tally_dates} tally dates
            </span>
          </div>
        </div>
      </div>

      <Pending title="No forecast has been generated for this SKU" reason={forecast?.reason}>
        <div className="pending__body">
          When Phase 3 of the pipeline runs, this card holds the 30-day forecast with its confidence band and the naive
          baseline. Note what the benchmark already found: for these mostly intermittent SKUs a persistence baseline is
          hard to beat, so the comparison against naive belongs here as prominently as the forecast line itself.
        </div>
      </Pending>

      <div className="grid-2">
        <Pending title="Accuracy metrics pending" reason={metrics?.reason} />
        <div className="card card__pad">
          <div className="card-h"><span className="section-h">Why this is blank</span></div>
          <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.55 }}>
            MAPE, RMSE and MAE must come from the model run recorded in <span className="mono">Result_Forecast_Metrics</span>,
            not from the frontend. Any number shown here that the pipeline did not produce would end up quoted in
            Chapter 4.
          </div>
        </div>
      </div>

      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">Observed Monthly Units — {product.item_name}</span>
          <span className="hint">actual tallied history · no fitted line, no projection</span>
        </div>
        <HistoryChart productId={product.product_id} />
      </div>
    </div>
  );
}

function HistoryChart({ productId }) {
  const { data, loading } = useData(() => getProductHistory(productId), [productId], []);
  if (loading) return <Loading />;
  if (!data || data.length === 0) return <div className="empty">No monthly history for this product.</div>;
  return <LineChart data={data.map(d => ({ label: shortMonth(d.month), value: d.units }))} height={240} />;
}
