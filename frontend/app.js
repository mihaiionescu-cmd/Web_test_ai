angular.module('testApp', [])
.controller('MainController', ['$scope', '$http', '$interval', '$timeout', function($scope, $http, $interval, $timeout) {
    $scope.sessions = [];
    $scope.selectedSession = null;
    $scope.generating = false;
    $scope.executing = false;
    $scope.generationSuccess = false;
    $scope.errorMessage = '';
    $scope.url = '';
    $scope.numTestCases = 5;
    $scope.autoRefreshEnabled = false;

    var autoRefreshInterval = null;
    var perSessionPollInterval = null;
    var execPollInterval = null;
    const API_BASE = '/api';

    // --- Helpers ---
    $scope.normalizeSteps = function(steps) {
        if (!steps) return '';
        try {
            if (typeof steps === 'string') {
                var trimmed = steps.trim();
                if ((trimmed.startsWith('[') && trimmed.endsWith(']')) ||
                    (trimmed.startsWith('{') && trimmed.endsWith('}'))) {
                    var parsed = JSON.parse(trimmed);
                    if (Array.isArray(parsed)) {
                        return parsed.map((s,i)=> (i+1)+'. '+s).join('\n');
                    } else if (typeof parsed === 'object' && parsed.steps) {
                        return parsed.steps.join('\n');
                    }
                }
            }
        } catch(e) {}
        return steps;
    };

    $scope.getStats = function(session) {
        var testCases = session.test_cases || [];
        return {
            total: testCases.length,
            passed: testCases.filter(tc => tc.status === 'Pass').length,
            failed: testCases.filter(tc => tc.status === 'Fail').length,
            pending: testCases.filter(tc => !tc.status || tc.status === 'Pending').length
        };
    };

    $scope.getProgress = function(session) {
        var stats = $scope.getStats(session);
        if (stats.total === 0) return 0;
        return Math.round(((stats.passed + stats.failed) / stats.total) * 100);
    };

    $scope.getStatusClass = function(status) {
        if (status === 'Pass') return 'pass';
        if (status === 'Fail') return 'fail';
        return 'pending';
    };

    $scope.getStatusSymbol = function(status) {
        if (status === 'Pass') return '✓';
        if (status === 'Fail') return '✗';
        return '○';
    };

    $scope.formatDate = function(dateString) {
        if (!dateString) return '';
        var date = new Date(dateString);
        if (isNaN(date.getTime())) return dateString;
        return date.toLocaleString('ro-RO');
    };

    // --- Fetch sessions ---
    $scope.fetchSessions = function() {
        $http.get(API_BASE + '/GetAllSessions')
            .then(response => {
                if (!Array.isArray(response.data)) { $scope.errorMessage = 'Unexpected sessions format'; return; }
                $scope.sessions = response.data.map(s => { s.test_cases = s.test_cases || []; s.status = s.status || 'In Progress'; return s; });
                if ($scope.selectedSession) {
                    var updated = $scope.sessions.find(s => s.session_id === $scope.selectedSession.session_id);
                    $scope.selectedSession = updated || null;
                }
            })
            .catch(() => $scope.errorMessage = 'Eroare la încărcarea sesiunilor');
    };

    $scope.fetchSessionById = function(sessionId) {
        return $http.get(API_BASE + '/GetSession/' + encodeURIComponent(sessionId))
            .then(response => {
                if (response.data && response.data.session) {
                    var s = response.data.session;
                    s.test_cases = response.data.test_cases || [];
                    s.status = s.status || 'In Progress';
                    return s;
                } else return null;
            })
            .catch(() => null);
    };

    function startAutoRefreshList() {
        if (autoRefreshInterval) $interval.cancel(autoRefreshInterval);
        $scope.autoRefreshEnabled = true;
        autoRefreshInterval = $interval(() => $scope.fetchSessions(), 5000);
    }
    function stopAutoRefreshList() {
        if (autoRefreshInterval) { $interval.cancel(autoRefreshInterval); autoRefreshInterval = null; }
        $scope.autoRefreshEnabled = false;
    }

    // --- Generate Tests ---
    $scope.generateTests = function() {
        if (!$scope.url || $scope.numTestCases < 1) { $scope.errorMessage = 'Completează URL-ul și numărul de teste'; return; }
        $scope.generating = true; $scope.generationSuccess = false; $scope.errorMessage = '';
        startAutoRefreshList();

        $http.post(API_BASE + '/generate-testcases', { url: $scope.url, num_test_cases: parseInt($scope.numTestCases,10) })
            .then(response => {
                var newSessionId = response.data?.session_id;
                $scope.fetchSessions();
                if (!newSessionId) { $scope.generating = false; $scope.generationSuccess = true; return; }

                var attempts = 0, maxAttempts = 36;
                if (perSessionPollInterval) $interval.cancel(perSessionPollInterval);

                perSessionPollInterval = $interval(() => {
                    attempts++;
                    $scope.fetchSessionById(newSessionId).then(session => {
                        if (session) {
                            var existing = $scope.sessions.find(s => s.session_id === session.session_id);
                            if (!existing) { $scope.sessions.unshift(session); } else { $scope.sessions[$scope.sessions.indexOf(existing)] = session; }
                            if (session.test_cases?.length > 0) {
                                $scope.selectSession(session);
                                $scope.generating = false; $scope.generationSuccess = true;
                                if (perSessionPollInterval) { $interval.cancel(perSessionPollInterval); perSessionPollInterval = null; }
                                $timeout(() => stopAutoRefreshList(), 10000);
                            }
                        }
                    });
                    if (attempts >= maxAttempts) {
                        $scope.generating = false; $scope.errorMessage = 'Timeout generare teste.';
                        if (perSessionPollInterval) { $interval.cancel(perSessionPollInterval); perSessionPollInterval = null; }
                        $timeout(() => stopAutoRefreshList(), 1000);
                    }
                }, 5000);
            })
            .catch(() => { $scope.errorMessage = 'Eroare la generare teste'; $scope.generating = false; stopAutoRefreshList(); });
    };

    // --- Execute Session ---
    $scope.executeSession = function(sessionId) {
        if ($scope.executing) return;
        $scope.executing = true; $scope.errorMessage = '';
        startAutoRefreshList();

        $http.post(API_BASE + '/execute-session/' + encodeURIComponent(sessionId))
            .then(() => {
                var attempts = 0, maxAttempts = 120;
                if (execPollInterval) $interval.cancel(execPollInterval);

                execPollInterval = $interval(() => {
                    attempts++;
                    $scope.fetchSessionById(sessionId).then(session => {
                        if (!session) { $scope.errorMessage = 'Sesiunea nu a fost găsită'; $scope.executing = false; if (execPollInterval) $interval.cancel(execPollInterval); return; }
                        $scope.selectedSession = session;
                        var idx = $scope.sessions.findIndex(s => s.session_id === session.session_id);
                        if (idx === -1) { $scope.sessions.unshift(session); } else { $scope.sessions[idx] = session; }

                        var allDone = session.test_cases?.every(tc => tc.status && tc.status !== 'Pending');
                        if (session.status?.toLowerCase() === 'completed' || allDone) {
                            $scope.executing = false;
                            if (execPollInterval) $interval.cancel(execPollInterval);
                            $timeout(() => stopAutoRefreshList(), 2000);
                        }
                    });
                    if (attempts >= maxAttempts) {
                        $scope.executing = false; $scope.errorMessage = 'Timeout execuție teste.';
                        if (execPollInterval) $interval.cancel(execPollInterval);
                        $timeout(() => stopAutoRefreshList(), 1000);
                    }
                }, 5000);
            })
            .catch(() => { $scope.errorMessage = 'Eroare execuție teste'; $scope.executing = false; stopAutoRefreshList(); });
    };

    // --- Export CSV ---
    $scope.exportToExcel = function(session) {
        if (!session) return;
        var headers = ['Test ID', 'Title', 'Description', 'Status', 'Comment', 'Steps', 'Executed At'];
        var rows = (session.test_cases || []).map(tc => [
            tc.test_id, tc.title || '', tc.description || '', tc.status || 'Pending', tc.comment || '', tc.steps || '', tc.executed_at || ''
        ]);
        var csv = headers.join(',') + '\n';
        rows.forEach(row => { csv += row.map(cell => '"' + String(cell).replace(/"/g,'""') + '"').join(',') + '\n'; });
        var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        var url = window.URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url; a.download = 'tests-' + session.session_id + '.csv';
        document.body.appendChild(a); a.click(); document.body.removeChild(a); window.URL.revokeObjectURL(url);
    };

    // --- Allure Report ---
    $scope.viewAllureReport = function() {
        window.open('http://localhost:8080/allure/index.html', '_blank');
    };

    // --- Select Session ---
    $scope.selectSession = function(session) {
        if (!session) return;
        $scope.fetchSessionById(session.session_id).then(full => { $scope.selectedSession = full || session; window.scrollTo({ top: 0, behavior: 'smooth' }); });
    };

    $scope.refreshSessions = function() { $scope.fetchSessions(); };

    $scope.$on('$destroy', function() {
        if (autoRefreshInterval) $interval.cancel(autoRefreshInterval);
        if (perSessionPollInterval) $interval.cancel(perSessionPollInterval);
        if (execPollInterval) $interval.cancel(execPollInterval);
    });

    $scope.fetchSessions();
}]);
