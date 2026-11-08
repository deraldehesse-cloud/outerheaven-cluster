package com.outerheaven.webidecompanion.data.api

import com.outerheaven.webidecompanion.data.model.*
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query
import java.util.concurrent.TimeUnit

interface MetalBridgeApi {

    @GET("api/status")
    suspend fun getStatus(): ClusterStatusResponse

    @GET("api/dropzone")
    suspend fun getDropzone(): DropzoneResponse

    @POST("api/pipeline/trigger")
    suspend fun triggerPipeline(): TriggerResponse

    @GET("api/pipeline/status")
    suspend fun getPipelineStatus(): PipelineStatusResponse

    @GET("api/findings/latest")
    suspend fun getFindings(
        @Query("package") packageName: String? = null
    ): FindingsResponse

    // REA-Sandbox Endpunkte
    @GET("api/sandbox/status")
    suspend fun getSandboxStatus(): SandboxStatus

    @POST("api/sandbox/container/restart")
    suspend fun restartSandboxContainer(): RestartContainerResponse

    @POST("api/sandbox/decompile")
    suspend fun decompileApk(
        @Body req: DecompileRequest
    ): DecompileResponse

    @GET("api/sandbox/jobs")
    suspend fun getSandboxJobs(): SandboxJobsResponse

    @GET("api/sandbox/outputs")
    suspend fun getSandboxOutputs(): SandboxOutputsResponse

    companion object {
        const val BASE_URL = "http://100.96.88.84:8444/"
        const val CODE_SERVER_URL = "http://100.96.88.84:8443"

        fun create(baseUrl: String = BASE_URL): MetalBridgeApi {
            val logging = HttpLoggingInterceptor().apply {
                level = HttpLoggingInterceptor.Level.BASIC
            }

            val client = OkHttpClient.Builder()
                .connectTimeout(5, TimeUnit.SECONDS)
                .readTimeout(20, TimeUnit.SECONDS)
                .addInterceptor(logging)
                .build()

            return Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
                .create(MetalBridgeApi::class.java)
        }
    }
}
