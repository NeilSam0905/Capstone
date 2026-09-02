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
  const { data: products, loading } = useData(() => getProducts(filters), [filters], [],
    { key: `forecast:products:${filters.supplier}|${filters.category}` });
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
    () => getProductForecast(productId), [productId], null,
    { key: `forecast:${productId}` }
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
          title="Accuracy metrics pending"
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
            <b>Unvalidated forecast:</b> Every SKU is forecast with the same rolling mean,
            but this one&rsquo;s history is too short to hold out a test window, so there are
            no accuracy metrics behind the line — treat it as a rough estimate.
          </div>
        )}
      </div>

      {/* Accuracy metrics */}
      <div className="card card__pad">
        <div className="card-h">
          <span className="section-h">Forecast Accuracy Metrics</span>
          <span className="hint">from Result_Forecast_Metrics · pipeline output, not computed by the frontend</span>
        </div>
        {fd.metrics && fd.metrics.length > 0 ? (
          <div style={{ overflowX: 'auto' }}>
            <table className="tbl" style={{ minWidth: 800 }}>
              <thead>
                <tr>
                  <th>Tier</th>
                  <th>Period</th>
                  <th>Validation</th>
                  <th className="num">n</th>
                  <th className="num">MAE</th>
                  <th className="num">RMSE</th>
                  <th className="num">MAPE</th>
                  <th className="num">Naive MAE</th>
                  <th className="num">Naive RMSE</th>
                  <th>Beats Naive?</th>
                </tr>
              </thead>
              <tbody>
                {fd.metrics.map((m, i) => (
                  <tr key={i}>
                    <td><span className="tag tag--gold">{m.tier}</span></td>
                    <td>{m.period_scope}</td>
                    <td className="hint">{m.validation_method}</td>
                    <td className="num">{m.n_obs}</td>
                    <td className="num">{m.mae != null ? m.mae.toFixed(2) : '—'}</td>
                    <td className="num">{m.rmse != null ? m.rmse.toFixed(2) : '—'}</td>
                    <td className="num">
                      {m.mape != null
                        ? <span style={m.meets_mape_threshold ? { color: 'var(--ok)', fontWeight: 700 } : undefined}>
                            {m.mape.toFixed(1)}%
                          </span>
                        : '—'}
                    </td>
                    <td className="num">{m.naive_mae != null ? m.naive_mae.toFixed(2) : '—'}</td>
                    <td className="num">{m.naive_rmse != null ? m.naive_rmse.toFixed(2) : '—'}</td>
                    <td>
                      {m.beats_naive_mae
                        ? <span className="tag tag--ok">Yes</span>
                        : <span className="tag tag--crit">No</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="hint">No accuracy metrics recorded for this SKU.</div>
        )}
      </div>
    </>
  );
}

function HistoryChart({ productId }) {
  const { data, loading } = useData(() => getProductHistory(productId), [productId], [],
    { key: `history:${productId}` });
  if (loading) return <Loading />;
  if (!data || data.length === 0) return <div className="empty">No monthly history for this product.</div>;
  return <LineChart data={data.map(d => ({ label: shortMonth(d.month), value: d.units }))} height={240} />;
}
