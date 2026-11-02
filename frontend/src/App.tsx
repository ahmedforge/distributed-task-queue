import React from 'react';
import { AuthProvider, RequireAccount } from './auth';
import { AccountPage } from './pages/AccountPage';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Layout } from './components/Layout';
import { JobListPage } from './pages/JobListPage';
import { SubmitJobPage } from './pages/SubmitJobPage';
import { JobDetailPage } from './pages/JobDetailPage';

const queryClient = new QueryClient();

export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route path="account" element={<AccountPage />} />
            <Route element={<RequireAccount />}>
            <Route index element={<JobListPage />} />
            <Route path="dlq" element={<JobListPage failedOnly />} />
            <Route path="submit" element={<SubmitJobPage />} />
            <Route path="jobs/:id" element={<JobDetailPage />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
};

export default App;
