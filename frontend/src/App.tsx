import React from 'react';
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
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<JobListPage />} />
            <Route path="submit" element={<SubmitJobPage />} />
            <Route path="jobs/:id" element={<JobDetailPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
};

export default App;
