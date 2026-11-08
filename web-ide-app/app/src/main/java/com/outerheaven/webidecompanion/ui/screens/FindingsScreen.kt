package com.outerheaven.webidecompanion.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.outerheaven.webidecompanion.data.model.FindingsResponse
import com.outerheaven.webidecompanion.ui.theme.*
import com.outerheaven.webidecompanion.ui.viewmodel.MainViewModel
import com.outerheaven.webidecompanion.ui.viewmodel.UiState

@Composable
fun FindingsScreen(
    viewModel: MainViewModel,
    modifier: Modifier = Modifier
) {
    val findingsState by viewModel.findingsState.collectAsState()
    val selectedPackage by viewModel.selectedPackage.collectAsState()
    val contentScrollState = rememberScrollState()
    val packageChipScrollState = rememberScrollState()

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
                onClick = { viewModel.loadFindings(selectedPackage) },
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

        when (val state = findingsState) {
            is UiState.Success -> {
                val findings = state.data
                val packages = findings.available_packages

                // Paket-Auswahl Chips
                if (packages.isNotEmpty()) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(packageChipScrollState),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        packages.forEach { pkg ->
                            val isSelected = (pkg.equals(findings.packageName, ignoreCase = true))
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

                // Content Viewer (maximiert auf den gesamten restlichen Screen)
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
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = AccentCyan)
                }
            }
            is UiState.Error -> {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = DarkSurface)
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text(
                            text = "Keine Findings verfügbar",
                            style = MaterialTheme.typography.titleMedium,
                            color = AccentOrange
                        )
                        Text(
                            text = state.message,
                            style = MaterialTheme.typography.bodyMedium,
                            color = TextSecondary
                        )
                        Button(
                            onClick = { viewModel.loadFindings() },
                            colors = ButtonDefaults.buttonColors(containerColor = DarkSurfaceVariant)
                        ) {
                            Text("Erneut versuchen")
                        }
                    }
                }
            }
            else -> {}
        }
    }
}
