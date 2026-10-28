import React, { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { AuthGuard } from 'lemma-sdk/react'
import { lemmaClient } from './lemma-client'
import { SessionProvider, useSession } from './lib/session'
import { ToastProvider, Card, Button } from './components/ui'
import { Shell, Route } from './components/Shell'
import { Dashboard } from './pages/Dashboard'
import { Reviews } from './pages/Reviews'
import { ReviewDetail } from './pages/ReviewDetail'
import { NewReview } from './pages/NewReview'
import { Guidelines } from './pages/Guidelines'
import { Facts } from './pages/Facts'
import { Assets } from './pages/Assets'
import { Approvals } from './pages/Approvals'
import { Analytics } from './pages/Analytics'
import { Insights } from './pages/Insights'
import { AuditLog } from './pages/AuditLog'
import { Team } from './pages/Team'
import { Settings } from './pages/Settings'
import { Quality } from './pages/Quality'
import { Campaigns } from './pages/Campaigns'
import { Provenance } from './pages/Provenance'
import { Visual } from './pages/Visual'
import { Risk } from './pages/Risk'
import { Policy } from './pages/Policy'
import { Creative } from './pages/Creative'
import { Publish } from './pages/Publish'
import { Chains } from './pages/Chains'

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  constructor(props: any) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (this.state.error) {
      return (
        <div className="content">
          <Card pad>
            <div className="err">
              <b>This screen could not be displayed.</b>
              <div style={{ marginTop: 6 }}>{this.state.error.message}</div>
            </div>
            <div style={{ marginTop: 12 }}>
              <Button variant="primary" onClick={() => { this.setState({ error: null }); window.location.hash = '#/dashboard' }}>
                Back to the dashboard
              </Button>
            </div>
          </Card>
        </div>
      )
    }
    return this.props.children
  }
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const [page, id] = raw.split('/')
  return { page: page || 'dashboard', id }
}

function Router() {
  const [route, setRoute] = useState<Route>(parseHash)

  useEffect(() => {
    const onHash = () => setRoute(parseHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const navigate = (r: Route) => {
    window.location.hash = `#/${r.page}${r.id ? `/${r.id}` : ''}`
    setRoute(r)
  }

  const session = useSession()
  if (!session.ready) {
    return (
      <div className="content">
        <Card pad>
          <div className="row">
            <span className="spinner" aria-hidden="true" />
            <span>Preparing your BrandGuard AI workspace — {session.stage}…</span>
          </div>
        </Card>
      </div>
    )
  }
  if (session.bootError) {
    return (
      <div className="content">
        <Card pad>
          <div className="err">
            <b>BrandGuard AI could not start.</b>
            <div>{session.bootError}</div>
          </div>
          <div style={{ marginTop: 12 }}>
            <Button variant="primary" onClick={() => window.location.reload()}>Retry</Button>
          </div>
        </Card>
      </div>
    )
  }
  if (session.orgs.filter((o) => session.memberships.some((m) => m.organization_id === o.id)).length === 0) {
    return (
      <div className="content">
        <Card pad>
          <div className="row">
            <AlertTriangle size={16} />
            <b>You are not a member of any BrandGuard AI workspace yet.</b>
          </div>
          <p className="muted" style={{ marginTop: 8 }}>
            An administrator of a workspace can invite you from Team &amp; permissions. If you are setting this up,
            open Settings to provision a workspace.
          </p>
        </Card>
      </div>
    )
  }

  const page = (() => {
    switch (route.page) {
      case 'reviews': return <Reviews navigate={navigate} />
      case 'review': return <ReviewDetail id={route.id!} navigate={navigate} />
      case 'new': return <NewReview navigate={navigate} />
      case 'guidelines': return <Guidelines />
      case 'facts': return <Facts />
      case 'assets': return <Assets />
      case 'approvals': return <Approvals navigate={navigate} />
      case 'analytics': return <Analytics navigate={navigate} />
      case 'insights': return <Insights />
      case 'audit': return <AuditLog />
      case 'team': return <Team />
      case 'settings': return <Settings />
      case 'quality': return <Quality />
      case 'campaigns': return <Campaigns navigate={navigate} />
      case 'provenance': return <Provenance navigate={navigate} />
      case 'visual': return <Visual navigate={navigate} />
      case 'risk': return <Risk navigate={navigate} />
      case 'policy': return <Policy navigate={navigate} />
      case 'creative': return <Creative navigate={navigate} />
      case 'publish': return <Publish navigate={navigate} />
      case 'chains': return <Chains />
      default: return <Dashboard navigate={navigate} />
    }
  })()

  return <Shell route={route} navigate={navigate}><ErrorBoundary key={`${route.page}/${route.id ?? ''}`}>{page}</ErrorBoundary></Shell>
}

export default function App() {
  return (
    <AuthGuard client={lemmaClient} appName="BrandGuard AI">
      <ToastProvider>
        <SessionProvider>
          <Router />
        </SessionProvider>
      </ToastProvider>
    </AuthGuard>
  )
}
