import Modal from './Modal';
import { ItemMovement } from './Advisories';
import { FSN_LABEL } from '../lib/format';

/**
 * One item's sales history in a dialog: units sold every tallied month, the
 * enrollment and start-of-term months marked, and its inventory counts (the
 * advisories' ItemMovement). Opened from the reorder lists - the adviser's
 * "when clicking an item, show its historical movement" (Nov 2026) - so an
 * order can be checked against how the item sold in past seasons.
 *
 * `item` needs product_id and item_name; null keeps it closed. `children`
 * go under the charts (a button onward, say).
 */
export default function ItemHistory({ item, onClose, children }) {
  if (!item) return null;
  const sub = [item.supplier_name, FSN_LABEL[item.fsn_class]].filter(Boolean).join(' · ');
  return (
    <Modal open onClose={onClose} width={860} title={`Sales History — ${item.item_name}`}
           subtitle={sub || undefined}>
      <ItemMovement productId={item.product_id} />
      {children}
    </Modal>
  );
}
