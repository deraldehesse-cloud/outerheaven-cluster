package com.outerheaven.webidecompanion.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.outerheaven.webidecompanion.ui.theme.*
import com.outerheaven.webidecompanion.ui.viewmodel.MainViewModel
import com.outerheaven.webidecompanion.ui.viewmodel.UiState

@Composable
fun FindingsScreen(
    viewModel: MainViewModel,
    modifier: Modifier = Modifier
) {
    val findingsState by viewModel.findingsState.collectAsState()
    val sandboxOutputsState by viewModel.sandboxOutputsState.collectAsState()
    val selectedPackage by viewModel.selectedPackage.collectAsState()
    val contentScrollState = rememberScrollState()
    val packageChipScrollState = rememberScrollState()

    // Paketauswahl basierend auf der Ordnerliste aus /api/sandbox/outputs
    val candidateFolders = (sandboxOutputsState as? UiState.Success)?.data?.folders?.filter { it.name != "catlord" } ?: emptyList()
    val availablePackagesFromFindings = (findingsState as? UiState.Success)?.data?.available_packages ?: emptyList()

    val packageList = remember(candidateFolders, availablePackagesFromFindings, selectedPackage) {
        val list = mutableListOf<String>()
        candidateFolders.forEach { folder ->
            if (folder.name.isNotBlank() && !list.contains(folder.name)) {
                list.add(folder.name)
            }
        }
        availablePackagesFromFindings.forEach { pkg ->
            if (pkg.isNotBlank() && !list.contains(pkg)) {
                list.add(pkg)
            }
        }
        selectedPackage?.let { sel ->
            if (sel.isNotBlank() && !list.contains(sel)) {
                list.add(sel)
            }
        }
        list
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(DarkBackground)
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "LLM FINDINGS & ANALYSEN",
                    style = MaterialTheme.typography.titleLarge,
                    color = AccentCyan,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.sp
                )
                Text(
                    text = "Auswertungen durch DeepSeek 6.7b",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }
            IconButton(
                onClick = {
                    viewModel.loadSandboxOutputs()
                    viewModel.loadFindings(selectedPackage)
                },
                modifier = Modifier
                    .clip(CircleShape)
                    .background(DarkSurfaceVariant)
            ) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Findings aktualisieren",
                    tint = AccentCyan
                )
            }
        }

        // 1. Obere Chip-Leiste zur Paketauswahl (IMMER anzeigen)
        if (packageList.isNotEmpty()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(packageChipScrollState),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                packageList.forEach { pkg ->
                    val isSelected = pkg.equals(selectedPackage, ignoreCase = true)
                    FilterChip(
                        selected = isSelected,
                        onClick = { viewModel.selectPackage(pkg) },
                        label = { Text(pkg) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = AccentCyan,
                            selectedLabelColor = DarkBackground,
                            containerColor = DarkSurface,
                            labelColor = TextSecondary
                        ),
                        border = FilterChipDefaults.filterChipBorder(
                            enabled = true,
                            selected = isSelected,
                            borderColor = if (isSelected) AccentCyan else DarkBorder
                        )
                    )
                }
            }
        }

        // 2. Textbereich / Findings Viewer / Fallback-Hinweiskarte
        when (val state = findingsState) {
            is UiState.Success -> {
                val findings = state.data
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .border(1.dp, DarkBorder, RoundedCornerShape(10.dp)),
                    colors = CardDefaults.cardColors(containerColor = DarkSurface)
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .verticalScroll(contentScrollState)
                            .padding(16.dp)
                    ) {
                        Text(
                            text = findings.content,
                            style = MaterialTheme.typography.bodyMedium,
                            fontFamily = FontFamily.Monospace,
                            color = TextPrimary,
                            lineHeight = 20.sp
                        )
                    }
                }
            }
            is UiState.Loading -> {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = AccentCyan)
                }
            }
            is UiState.Error -> {
                val isNotFound = state.message == "NOT_FOUND" ||
                        state.message.contains("404") ||
                        state.message.contains("Keine Findings", ignoreCase = true)

                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .border(1.dp, DarkBorder, RoundedCornerShape(10.dp)),
                    colors = CardDefaults.cardColors(containerColor = DarkSurface)
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .verticalScroll(rememberScrollState())
                            .padding(24.dp),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Icon(
                            imageVector = Icons.Default.Info,
                            contentDescription = null,
                            tint = if (isNotFound) AccentCyan else AccentOrange,
                            modifier = Modifier.size(48.dp)
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            text = if (isNotFound) "Keine LLM-Analyse vorhanden" else "Fehler beim Laden",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = if (isNotFound) AccentCyan else AccentOrange
                        )
                        if (!selectedPackage.isNullOrBlank()) {
                            Spacer(modifier = Modifier.height(4.dp))
                            Text(
                                text = "Paket: $selectedPackage",
                                style = MaterialTheme.typography.bodySmall,
                                color = TextMuted
                            )
                        }
                        Spacer(modifier = Modifier.height(12.dp))
                        Text(
                            text = if (isNotFound)
                                "Für dieses Paket wurde noch keine LLM-Analyse generiert. Bitte in der Sandbox oder Pipeline anstoßen."
                            else
                                state.message,
                            style = MaterialTheme.typography.bodyMedium,
                            color = TextSecondary,
                            textAlign = TextAlign.Center,
                            lineHeight = 20.sp
                        )
                        Spacer(modifier = Modifier.height(20.dp))
                        Button(
                            onClick = { viewModel.loadFindings(selectedPackage) },
                            colors = ButtonDefaults.buttonColors(containerColor = DarkSurfaceVariant),
                            border = BorderStroke(1.dp, DarkBorder)
                        ) {
                            Icon(
                                imageVector = Icons.Default.Refresh,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp),
                                tint = AccentCyan
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text("Erneut prüfen", color = TextPrimary)
                        }
                    }
                }
            }
            is UiState.Idle -> {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center
                ) {
                    Text("Wähle ein Paket aus", color = TextSecondary)
                }
            }
        }
    }
}
