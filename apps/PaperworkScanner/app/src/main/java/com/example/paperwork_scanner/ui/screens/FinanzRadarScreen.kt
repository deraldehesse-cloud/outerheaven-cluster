package com.example.paperwork_scanner.ui.screens

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalanceWallet
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.TrendingDown
import androidx.compose.material.icons.filled.TrendingUp
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.paperwork_scanner.tailscale.TailscaleManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.text.NumberFormat
import java.util.Locale
import java.util.concurrent.TimeUnit

data class BudgetInvoice(
    val title: String,
    val date: String,
    val amount: Double
)

data class BudgetSummaryData(
    val monthName: String,
    val monthlyIncome: Double,
    val fixRent: Double,
    val fixUtilities: Double,
    val fixPhone: Double,
    val fixGroceries: Double,
    val fixedCostsTotal: Double,
    val invoicesTotal: Double,
    val remainingBudget: Double,
    val coachAdvice: String,
    val invoices: List<BudgetInvoice>
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FinanzRadarScreen(
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()
    val scrollState = rememberScrollState()

    var summaryData by remember { mutableStateOf<BudgetSummaryData?>(null) }
    var isLoading by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var showEditDialog by remember { mutableStateOf(false) }

    // Edit state
    var editIncome by remember { mutableStateOf("") }
    var editRent by remember { mutableStateOf("") }
    var editUtilities by remember { mutableStateOf("") }
    var editPhone by remember { mutableStateOf("") }
    var editGroceries by remember { mutableStateOf("") }
    var isSavingConfig by remember { mutableStateOf(false) }

    val okHttpClient = remember {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .build()
    }

    val currencyFormatter = remember {
        NumberFormat.getCurrencyInstance(Locale.GERMANY)
    }

    fun loadBudgetSummary(showToast: Boolean = false) {
        coroutineScope.launch {
            isLoading = true
            errorMessage = null
            try {
                val baseUrl = withContext(Dispatchers.IO) { TailscaleManager.getHermesBaseUrl() }
                val request = Request.Builder()
                    .url("$baseUrl/api/budget/summary")
                    .get()
                    .build()

                val response = withContext(Dispatchers.IO) { okHttpClient.newCall(request).execute() }
                val code = response.code
                val bodyStr = response.body?.string().orEmpty()
                response.close()

                if (code == 200) {
                    val json = JSONObject(bodyStr)
                    val fixedObj = json.optJSONObject("fixed_costs") ?: JSONObject()
                    val invArray = json.optJSONArray("invoices") ?: JSONArray()
                    val parsedInvoices = mutableListOf<BudgetInvoice>()
                    for (i in 0 until invArray.length()) {
                        val inv = invArray.getJSONObject(i)
                        parsedInvoices.add(
                            BudgetInvoice(
                                title = inv.optString("title", "Rechnung"),
                                date = inv.optString("date", ""),
                                amount = inv.optDouble("amount", 0.0)
                            )
                        )
                    }

                    summaryData = BudgetSummaryData(
                        monthName = json.optString("month_name", "Aktueller Monat"),
                        monthlyIncome = json.optDouble("monthly_income", 0.0),
                        fixRent = fixedObj.optDouble("rent", 0.0),
                        fixUtilities = fixedObj.optDouble("utilities", 0.0),
                        fixPhone = fixedObj.optDouble("phone", 0.0),
                        fixGroceries = fixedObj.optDouble("groceries", 0.0),
                        fixedCostsTotal = fixedObj.optDouble("total", 0.0),
                        invoicesTotal = json.optDouble("invoices_total", 0.0),
                        remainingBudget = json.optDouble("remaining_budget", 0.0),
                        coachAdvice = json.optString("coach_advice", ""),
                        invoices = parsedInvoices
                    )
                    if (showToast) {
                        Toast.makeText(context, "Finanz-Radar aktualisiert", Toast.LENGTH_SHORT).show()
                    }
                } else {
                    errorMessage = "Server antwortete mit Code $code"
                }
            } catch (e: Exception) {
                errorMessage = "Verbindung zu Hermes nicht möglich: ${e.localizedMessage}"
            } finally {
                isLoading = false
            }
        }
    }

    fun openEditDialog() {
        summaryData?.let {
            editIncome = if (it.monthlyIncome > 0) it.monthlyIncome.toString() else "1013.00"
            editRent = if (it.fixRent > 0) it.fixRent.toString() else "450.00"
            editUtilities = if (it.fixUtilities > 0) it.fixUtilities.toString() else "65.00"
            editPhone = if (it.fixPhone > 0) it.fixPhone.toString() else "39.99"
            editGroceries = if (it.fixGroceries > 0) it.fixGroceries.toString() else "250.00"
        } ?: run {
            editIncome = "1013.00"
            editRent = "450.00"
            editUtilities = "65.00"
            editPhone = "39.99"
            editGroceries = "250.00"
        }
        showEditDialog = true
    }

    fun saveConfig() {
        coroutineScope.launch {
            isSavingConfig = true
            try {
                val baseUrl = withContext(Dispatchers.IO) { TailscaleManager.getHermesBaseUrl() }
                val payload = JSONObject().apply {
                    put("MONTHLY_INCOME", editIncome.toDoubleOrNull() ?: 1013.0)
                    put("FIX_RENT", editRent.toDoubleOrNull() ?: 450.0)
                    put("FIX_UTILITIES", editUtilities.toDoubleOrNull() ?: 65.0)
                    put("FIX_PHONE", editPhone.toDoubleOrNull() ?: 39.99)
                    put("FIX_GROCERIES", editGroceries.toDoubleOrNull() ?: 250.0)
                }

                val reqBody = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
                val request = Request.Builder()
                    .url("$baseUrl/api/budget/config")
                    .post(reqBody)
                    .build()

                val response = withContext(Dispatchers.IO) { okHttpClient.newCall(request).execute() }
                val code = response.code
                response.close()

                if (code in 200..299) {
                    showEditDialog = false
                    Toast.makeText(context, "Budget-Werte erfolgreich gespeichert", Toast.LENGTH_SHORT).show()
                    loadBudgetSummary()
                } else {
                    Toast.makeText(context, "Fehler beim Speichern: HTTP $code", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(context, "Netzwerkfehler: ${e.localizedMessage}", Toast.LENGTH_LONG).show()
            } finally {
                isSavingConfig = false
            }
        }
    }

    LaunchedEffect(Unit) {
        loadBudgetSummary()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "💶 Finanz-Radar",
                            style = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.Bold)
                        )
                    }
                },
                actions = {
                    IconButton(
                        onClick = { loadBudgetSummary(showToast = true) },
                        enabled = !isLoading
                    ) {
                        Icon(
                            imageVector = Icons.Default.Refresh,
                            contentDescription = "Aktualisieren"
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                )
            )
        }
    ) { innerPadding ->
        Column(
            modifier = modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(scrollState)
                .padding(horizontal = 16.dp, vertical = 8.dp)
        ) {
            // Status-Pille (Neutral, keine IPs)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Surface(
                    color = MaterialTheme.colorScheme.primaryContainer,
                    shape = RoundedCornerShape(16.dp)
                ) {
                    Text(
                        text = summaryData?.monthName ?: "Monats-Übersicht",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)
                    )
                }

                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(16.dp)
                ) {
                    Text(
                        text = "● Hermes Radar Aktiv",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                    )
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            if (isLoading && summaryData == null) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(200.dp),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator()
                }
            } else if (errorMessage != null && summaryData == null) {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer
                    ),
                    shape = RoundedCornerShape(16.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text(
                            text = "Finanz-Radar nicht erreichbar",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                            color = MaterialTheme.colorScheme.onErrorContainer
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = errorMessage ?: "Unbekannter Fehler",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            textAlign = TextAlign.Center
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                        Button(
                            onClick = { loadBudgetSummary() },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = MaterialTheme.colorScheme.error
                            )
                        ) {
                            Text("Erneut versuchen")
                        }
                    }
                }
            } else if (summaryData != null) {
                val data = summaryData!!
                val isPositive = data.remainingBudget >= 0

                // 1. Highlight-Card: Reales Restgeld
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = if (isPositive) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.errorContainer
                    ),
                    shape = RoundedCornerShape(20.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(20.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text(
                            text = "Reales Restgeld",
                            style = MaterialTheme.typography.labelLarge,
                            fontWeight = FontWeight.Medium,
                            color = if (isPositive) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onErrorContainer
                        )
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = currencyFormatter.format(data.remainingBudget),
                            fontSize = 38.sp,
                            fontWeight = FontWeight.ExtraBold,
                            color = if (isPositive) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error
                        )
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = if (isPositive)
                                "Frei verfügbar nach Abzug aller Fixkosten & Monatsrechnungen"
                            else
                                "Achtung: Fixkosten und Rechnungen übersteigen die Einnahmen!",
                            style = MaterialTheme.typography.bodySmall,
                            textAlign = TextAlign.Center,
                            color = if (isPositive) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onErrorContainer
                        )
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))

                // 2. Drei Kern-Metriken (Einnahmen, Fixkosten, Rechnungen)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    // Einnahmen
                    Card(
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(14.dp),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(
                                    imageVector = Icons.Default.TrendingUp,
                                    contentDescription = null,
                                    tint = MaterialTheme.colorScheme.primary,
                                    modifier = Modifier.size(16.dp)
                                )
                                Spacer(modifier = Modifier.width(4.dp))
                                Text("Einkommen", style = MaterialTheme.typography.labelSmall)
                            }
                            Spacer(modifier = Modifier.height(6.dp))
                            Text(
                                text = currencyFormatter.format(data.monthlyIncome),
                                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold)
                            )
                        }
                    }

                    // Fixkosten
                    Card(
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(14.dp),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(
                                    imageVector = Icons.Default.TrendingDown,
                                    contentDescription = null,
                                    tint = MaterialTheme.colorScheme.secondary,
                                    modifier = Modifier.size(16.dp)
                                )
                                Spacer(modifier = Modifier.width(4.dp))
                                Text("Fixkosten", style = MaterialTheme.typography.labelSmall)
                            }
                            Spacer(modifier = Modifier.height(6.dp))
                            Text(
                                text = currencyFormatter.format(data.fixedCostsTotal),
                                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold)
                            )
                        }
                    }

                    // Rechnungen (Paperless)
                    Card(
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(14.dp),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(
                                    imageVector = Icons.Default.AccountBalanceWallet,
                                    contentDescription = null,
                                    tint = MaterialTheme.colorScheme.tertiary,
                                    modifier = Modifier.size(16.dp)
                                )
                                Spacer(modifier = Modifier.width(4.dp))
                                Text("Belege", style = MaterialTheme.typography.labelSmall)
                            }
                            Spacer(modifier = Modifier.height(6.dp))
                            Text(
                                text = currencyFormatter.format(data.invoicesTotal),
                                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold)
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))

                // 3. Feste Ausgaben Aufschlüsselung & Edit Button
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f))
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "Fixkosten-Übersicht",
                                style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold)
                            )
                            FilledTonalButton(
                                onClick = { openEditDialog() },
                                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp),
                                shape = RoundedCornerShape(8.dp)
                            ) {
                                Icon(
                                    imageVector = Icons.Default.Edit,
                                    contentDescription = null,
                                    modifier = Modifier.size(14.dp)
                                )
                                Spacer(modifier = Modifier.width(4.dp))
                                Text("Anpassen", fontSize = 12.sp)
                            }
                        }

                        Spacer(modifier = Modifier.height(10.dp))
                        CostRow("Miete (kalt + warm)", data.fixRent, currencyFormatter)
                        CostRow("Nebenkosten / Strom", data.fixUtilities, currencyFormatter)
                        CostRow("Handy / Internet", data.fixPhone, currencyFormatter)
                        CostRow("Lebensmittel / Pauschale", data.fixGroceries, currencyFormatter)
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))

                // 4. Spar-Coach-Card (Empathetischer Rat von Hermes / Catlord)
                if (data.coachAdvice.isNotBlank()) {
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(16.dp),
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.tertiaryContainer.copy(alpha = 0.7f)
                        )
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Box(
                                    modifier = Modifier
                                        .size(32.dp)
                                        .background(MaterialTheme.colorScheme.tertiary, CircleShape),
                                    contentAlignment = Alignment.Center
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.Lightbulb,
                                        contentDescription = null,
                                        tint = MaterialTheme.colorScheme.onTertiary,
                                        modifier = Modifier.size(18.dp)
                                    )
                                }
                                Spacer(modifier = Modifier.width(10.dp))
                                Text(
                                    text = "Spar-Coach Hermes",
                                    style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                                    color = MaterialTheme.colorScheme.onTertiaryContainer
                                )
                            }
                            Spacer(modifier = Modifier.height(10.dp))
                            Text(
                                text = data.coachAdvice,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onTertiaryContainer,
                                lineHeight = 20.sp
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }

    // Dialog zum Bearbeiten des Budgets
    if (showEditDialog) {
        AlertDialog(
            onDismissRequest = { if (!isSavingConfig) showEditDialog = false },
            title = {
                Text("Monats-Budget anpassen", fontWeight = FontWeight.Bold)
            },
            text = {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Text(
                        "Passe hier dein Einkommen und deine festen monatlichen Pauschalen an. Die Werte werden sicher auf dem Hermes-Agenten gespeichert.",
                        style = MaterialTheme.typography.bodySmall
                    )

                    OutlinedTextField(
                        value = editIncome,
                        onValueChange = { editIncome = it },
                        label = { Text("Monatliches Einkommen (€)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = editRent,
                        onValueChange = { editRent = it },
                        label = { Text("Miete (€)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = editUtilities,
                        onValueChange = { editUtilities = it },
                        label = { Text("Nebenkosten / Strom (€)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = editPhone,
                        onValueChange = { editPhone = it },
                        label = { Text("Handy / Internet (€)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = editGroceries,
                        onValueChange = { editGroceries = it },
                        label = { Text("Lebensmittel-Pauschale (€)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            },
            confirmButton = {
                Button(
                    onClick = { saveConfig() },
                    enabled = !isSavingConfig
                ) {
                    if (isSavingConfig) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(16.dp),
                            color = MaterialTheme.colorScheme.onPrimary,
                            strokeWidth = 2.dp
                        )
                        Spacer(modifier = Modifier.width(6.dp))
                    }
                    Text("Speichern")
                }
            },
            dismissButton = {
                TextButton(
                    onClick = { showEditDialog = false },
                    enabled = !isSavingConfig
                ) {
                    Text("Abbrechen")
                }
            }
        )
    }
}

@Composable
private fun CostRow(label: String, amount: Double, formatter: NumberFormat) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(text = label, style = MaterialTheme.typography.bodyMedium)
        Text(
            text = formatter.format(amount),
            style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold)
        )
    }
}
