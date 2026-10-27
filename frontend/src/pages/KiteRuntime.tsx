import { Dashboard } from './Dashboard';
import { ViewQueryProvider } from '../components/sim/ViewQueryProvider';
import { useLiveResync } from '../hooks/useLiveResync';
import { LiveFeedBadge } from '../components/live/LiveFeedBadge';
import { BrokerLoginEntry } from '../components/auth/BrokerLoginEntry';
function Runtime() { useLiveResync(); return <><Dashboard /><LiveFeedBadge /><BrokerLoginEntry /></>; }
export default function KiteRuntime() { return <ViewQueryProvider><Runtime /></ViewQueryProvider>; }
