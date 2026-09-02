import { useState, useMemo } from 'react';
import { getProducts, getForecast, getProductForecast, getProductHistory } from '../services/dataService';
import useData from '../hooks/useData';
import Pending, { Loading } from '../components/Pending';
import { LineChart, ForecastChart } from '../components/charts';
import { num, shortMonth, usDate, FSN_TONE, FSN_LABEL } from '../lib/format';

/**
 * Demand Forecast.
 *
 * When Result_Forecast exists in the database (step4_forecast_model.py has
 * run), this screen shows the 30-day forecast with a confidence band and
 * accuracy metrics. When it doesn't, it shows the pending state and the
 * product's real observed monthly history — no fabricated numbers.
 */
export default function Forecast({ filters }) {
  const { data: products, loading } = useData(() => getProducts(filters), [filters], []);
  const { data: forecastMeta } = useData(getForecast, []);
  const [selectedId, setSelectedId] = useState(null);

  // Only SKUs step4_forecast_model.py actually produced a forecast for
  // (the Fast tier) belong in this dropdown - every other SKU would just
  // open onto the "no forecast" pending state, which is pointless to pick
  // from a list of hundreds. Falls back to every SKU with sales history if
  // the pipeline hasn't been run at all yet, so the pending state still has
  // something to show.
  const forecastableIds = useMemo(
    () => forecastMeta?.data?.products ? new Set(forecastMeta.data.products.map(p => p.product_id)) : null,
    [forecastMeta]
  );
  const withHistory = useMemo(() => {
    const withSales = products.filter(p => p.total_units > 0);
    const filtered = forecastableIds ? withSales.filter(p => forecastableIds.has(p.product_id)) : withSales;
    return filtered.sort((a, b) => b.total_units - a.total_units);
  }, [products, forecastableIds]);
  const product = withHistory.find(p => p.product_id === selectedId) ?? withHistory[0] ?? null;

  if (loading) return <Loading label="Loading products…" />;
  if (!product) {
    return (
      <div className="empty">
        {forecastableIds
          ? "No forecasted products match this filter."
          : "No products match this filter."}
      </div>
    );
  }

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

      {/* Forecast chart — real data or pending */}
      <ForecastPanel productId={product.product_id} forecastMeta={forecastMeta} />

      {/* Observed monthly history — always available */}
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

function ForecastPanel({ productId, forecastMeta }) {
  const { data: forecast, loading } = useData(
    () => getProductForecast(productId), [productId]
  );

  if (loading) return <Loading label="Loading forecast…" />;

  // No forecast available at all (pipeline not run)
  if (!forecastMeta?.available || !forecast?.available) {
    return (
      <>
        {/* Both cards take their explanation from the API's `reason` (see
            app.py's FORECAST_PENDING_REASON) rather than a string hardcoded
            here, so the wording is changed in one place. */}
        <Pending
          title="No forecast has been generated for this SKU"
          reason={forecast?.reason ?? forecastMeta?.reason}
        />
        <Pending
          title="Reliability check pending"
          reason={forecast?.reason ?? forecastMeta?.reason}
        />
      </>
    );
  }

  // Forecast data is available — render it
  const fd = forecast.data;

  return (
    <>
      {/* Forecast chart */}
      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">30-Day Demand Forecast — {fd.item_name}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="tag tag--gold">{fd.model_type}</span>
            {fd.is_heuristic && <span className="tag tag--warn">Unvalidated</span>}
            <span className="hint">Generated {usDate(fd.snapshot_date)}</span>
          </div>
        </div>
        <ForecastChart data={fd.forecast} />
        <div className="legend" style={{ justifyContent: 'center', marginTop: 10 }}>
          <span><i style={{ background: 'var(--accent)' }} />Forecast (ŷ)</span>
          <span><i style={{ background: 'var(--accent)', opacity: 0.15 }} />Confidence band</span>
        </div>

        {fd.is_heuristic && (
          <div className="notice notice--warn" style={{ marginTop: 12 }}>
            Not enough sales history yet to double-check this forecast — treat it as a rough estimate.
          </div>
        )}
      </div>

      {/* Reliability — plain-language summary, no raw error metrics */}
      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">How Reliable Is This Forecast?</span>
        </div>
        <ReliabilitySummary metrics={fd.metrics} isHeuristic={fd.is_heuristic} />
      </div>
    </>
  );
}

/**
 * Translates Result_Forecast_Metrics into one plain-language badge and
 * sentence instead of a raw MAE/RMSE/MAPE table. MAPE is deliberately never
 * shown here: on this dataset it's undefined whenever a period had zero
 * actual sales (the common case) and reads as 100%+ even for a working
 * forecast, so surfacing it to a non-technical reader does more harm than
 * good (see docs/DEGENERATE_FORECAST.md, docs/SPARSE_DEMAND_EXPERIMENTS.md).
 */
function ReliabilitySummary({ metrics, isHeuristic }) {
  if (isHeuristic) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="tag tag--warn">Not enough history yet</span>
        <span className="hint">This item hasn&rsquo;t sold long enough to check this forecast against real results.</span>
      </div>
    );
  }

  const overall = metrics?.find(m => m.period_scope === 'overall') ?? metrics?.[0];
  if (!overall || overall.mae == null) {
    return <div className="hint">No accuracy check recorded for this SKU yet.</div>;
  }

  const reliable = !!overall.beats_naive_mae;
  const typicalOff = Math.round(overall.mae);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className={`tag tag--${reliable ? 'ok' : 'warn'}`}>
          {reliable ? 'Reliable' : 'Rough estimate'}
        </span>
        <span className="hint">
          {reliable
            ? 'More accurate than just repeating last month’s number.'
            : 'No more accurate than repeating last month’s number — use with caution.'}
        </span>
      </div>
      <span className="hint">
        Checked against {overall.n_obs} past 30-day period{overall.n_obs === 1 ? '' : 's'} of real sales —
        actual sales were typically about {typicalOff} unit{typicalOff === 1 ? '' : 's'} away from this forecast.
      </span>
    </div>
  );
}

function HistoryChart({ productId }) {
  const { data, loading } = useData(() => getProductHistory(productId), [productId], []);
  if (loading) return <Loading />;
  if (!data || data.length === 0) return <div className="empty">No monthly history for this product.</div>;
  return <LineChart data={data.map(d => ({ label: shortMonth(d.month), value: d.units }))} height={240} />;
}
